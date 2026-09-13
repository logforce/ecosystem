// SPDX-License-Identifier: Apache-2.0
// Exercises built binaries and the actual C shared library, then shuts the daemon down.
import { spawn, spawnSync } from 'node:child_process';
import { mkdtempSync, realpathSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const scratch = realpathSync(mkdtempSync(path.join(tmpdir(), 'cog-smoke-')));
const socket = path.join(scratch, 'runtime', 's');
const daemon = spawn(path.join(root, 'target/debug/ecosd'), ['--socket', socket], { cwd: root });
const exited = new Promise(resolve => {
  daemon.once('error', error => resolve({ error }));
  daemon.once('exit', (code, signal) => resolve({ code, signal }));
});
let timer;
try {
  const ready = new Promise((resolve, reject) => {
    let output = '';
    daemon.stdout.on('data', chunk => { output += chunk; if (output.includes('READY ')) resolve(); });
    daemon.stderr.on('data', chunk => process.stderr.write(chunk));
    daemon.once('error', reject);
    daemon.once('exit', code => reject(new Error(`daemon exited before ready: ${code}`)));
    timer = setTimeout(() => reject(new Error('daemon startup timed out')), 5000);
  });
  await ready; clearTimeout(timer);
  function run(command, args) {
    const result = spawnSync(command, args, { cwd: root, encoding: 'utf8', timeout: 15000 });
    if (result.error || result.status !== 0) throw new Error(result.error?.message ?? `${command}: ${result.stderr}`);
    if (result.stdout) process.stdout.write(result.stdout);
  }
  run(path.join(root, 'target/debug/cog'), ['--socket', socket, 'demo']);
  const libraryDir = path.join(root, 'target/debug');
  const executable = path.join(scratch, 'mock-client');
  run('cc', ['-std=c11', '-Wall', '-Wextra', '-Werror', '-Iinclude', 'examples/mock-client.c',
    '-L', libraryDir, '-lcogposix', `-Wl,-rpath,${libraryDir}`, '-o', executable]);
  run(executable, [socket]);
  const abiTest = path.join(scratch, 'c-abi-test');
  run('cc', ['-std=c11', '-Wall', '-Wextra', '-Werror', '-pthread', '-Iinclude', 'tests/c-abi.c',
    '-L', libraryDir, '-lcogposix', `-Wl,-rpath,${libraryDir}`, '-o', abiTest]);
  run(abiTest, [socket]);
  if (process.platform === 'linux') {
    const ancillary = path.join(scratch, 'c-ancillary-test');
    run('cc', ['-std=c11', '-Wall', '-Wextra', '-Werror', 'tests/c-ancillary.c',
      'crates/cog-shm/src/linux.c', '-o', ancillary]);
    run(ancillary, []);
    const shmTest = path.join(scratch, 'c-shm-test');
    run('cc', ['-std=c11', '-Wall', '-Wextra', '-Werror', '-Iinclude', 'tests/c-shm.c',
      '-L', libraryDir, '-lcogposix', `-Wl,-rpath,${libraryDir}`, '-o', shmTest]);
    run(shmTest, [socket]);
    run(path.join(root, 'target/debug/cog'), ['--socket', socket, 'transport-bench']);
  }
  daemon.kill('SIGTERM');
  const result = await Promise.race([exited, new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error('daemon shutdown timed out')), 5000);
  })]);
  if (result.error || result.code !== 0) throw new Error(`daemon shutdown failed: ${JSON.stringify(result)}`);
  console.log('CLI and C ABI smoke tests passed; daemon stopped.');
} catch (error) {
  console.error(error.message); process.exitCode = 1;
} finally {
  clearTimeout(timer);
  if (daemon.exitCode === null && daemon.signalCode === null) daemon.kill('SIGKILL');
  await exited;
  rmSync(scratch, { recursive: true, force: true });
}
