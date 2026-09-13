// SPDX-License-Identifier: Apache-2.0
import { spawnSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { chmodSync, cpSync, mkdtempSync, realpathSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { collectSnapshot, exportSnapshot } from './publication.mjs';

function run(command, args, options = {}) {
  console.log(`> ${command} ${args.join(' ')}`);
  const result = spawnSync(command, args, { stdio: 'inherit', timeout: 20 * 60 * 1000, ...options });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} failed: ${result.status ?? result.signal}`);
}

let scratch;
let container;
try {
  if (process.argv[2] === '--inside') {
    if (process.platform !== 'linux' || process.getuid() === 0) {
      throw new Error('Container validation requires non-root Linux');
    }
    cpSync('/source', '/work/project', { recursive: true });
    process.chdir('/work/project');
    run('uname', ['-a']);
    run('rustc', ['--version']);
    run('node', ['--version']);
    run('cc', ['--version']);
    run('cargo', ['build', '--workspace', '--offline', '--locked']);
    run('cargo', ['fmt', '--all', '--', '--check']);
    run('cargo', ['clippy', '--workspace', '--all-targets', '--offline', '--locked', '--', '-D', 'warnings']);
    run('cargo', ['test', '--workspace', '--offline', '--locked']);
    run('node', ['scripts/smoke.mjs']);
    run('node', ['--test', 'tests/publication.test.mjs']);
    run('node', ['scripts/publication.mjs', 'check']);
    console.log('Linux validation passed as non-root with networking disabled.');
  } else {
    if (process.argv.length !== 2) throw new Error('Usage: node scripts/validate-linux.mjs');
    const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
    const snapshot = collectSnapshot(root);
    scratch = realpathSync(mkdtempSync(path.join(tmpdir(), 'cog-linux-')));
    const source = exportSnapshot(snapshot, path.join(scratch, 'source'));
    // The host may use umask 077; only this disposable public tree is made readable.
    const directories = new Set([source]);
    for (const file of snapshot.files) {
      let directory = path.dirname(path.join(source, file.path));
      while (directory !== source) {
        directories.add(directory);
        directory = path.dirname(directory);
      }
      chmodSync(path.join(source, file.path), 0o644);
    }
    for (const directory of directories) chmodSync(directory, 0o755);
    console.log(`Validating public snapshot ${snapshot.digest} (${snapshot.files.length} files)`);
    const image = 'ecos-cogposix-validation:rust-1.90.0-node-22.19.0';
    run('docker', ['build', '--platform', 'linux/amd64', '-t', image,
      '-f', path.join(source, 'containers/validation.Dockerfile'), source]);
    container = `cog-validation-${randomUUID()}`;
    run('docker', ['run', '--rm', '--name', container, '--platform', 'linux/amd64', '--network', 'none',
      '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
      '--pids-limit', '256', '--memory', '2g', '--cpus', '2',
      '--tmpfs', '/tmp:rw,exec,mode=1777,size=256m',
      '--tmpfs', '/work:rw,exec,mode=1777,size=1g',
      '--mount', `type=bind,source=${source},target=/source,readonly`,
      image, 'node', '/source/scripts/validate-linux.mjs', '--inside']);
    container = undefined;
  }
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
} finally {
  // A timed-out Docker client does not necessarily stop its container.
  if (container) {
    spawnSync('docker', ['rm', '-f', container], { stdio: 'inherit', timeout: 30000 });
  }
  if (scratch) rmSync(scratch, { recursive: true, force: true });
}
