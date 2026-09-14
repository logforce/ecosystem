// SPDX-License-Identifier: Apache-2.0
import { spawn, spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const python = '/opt/ecos-onnx/bin/python';
const model = '/opt/ecos-models/mnist-8.onnx';
const scratch = realpathSync(mkdtempSync(path.join(tmpdir(), 'cog-onnx-')));
const socket = path.join(scratch, 'runtime', 's');
let daemon, timer;
let exited = Promise.resolve();
function run(command, args) {
  const result = spawnSync(command, args, { cwd: root, encoding: 'utf8', timeout: 30000 });
  if (result.error || result.status !== 0) throw new Error(result.error?.message ?? `${command}: ${result.stderr}`);
  return result.stdout;
}
try {
  for (const flag of ['--worker-python', '--worker-script', '--mnist-model']) {
    const missing = spawnSync(path.join(root, 'target/debug/ecosd'), ['--socket', socket, flag], {
      encoding: 'utf8', timeout: 2000,
    });
    if (missing.error || missing.status === 0 || !missing.stderr.includes('requires a path')) {
      throw new Error(`missing worker path did not fail explicitly: ${flag}`);
    }
  }
  const oracle = JSON.parse(run(python, ['-I', 'tests/onnx-reference.py', model, path.join(scratch, 'samples')]));
  // Run the real worker directly once so sandbox/startup errors remain diagnosable.
  const firstRequest = Buffer.alloc(792); firstRequest.writeBigUInt64LE(1n);
  const direct = spawnSync(python, ['-I', '-u', 'workers/onnx_worker.py', model, String(process.pid)], {
    cwd: root, input: firstRequest, timeout: 20000,
    env: { OPENBLAS_NUM_THREADS: '1', OMP_NUM_THREADS: '1' },
  });
  if (direct.error || direct.status !== 0 || !direct.stdout.subarray(0, 5).equals(Buffer.from('COGW1'))) {
    throw new Error(`restricted worker: ${direct.error ?? direct.stderr.toString()}`);
  }
  const badModel = path.join(scratch, 'unapproved.onnx');
  writeFileSync(badModel, Buffer.alloc(26454));
  const bad = spawnSync(python, ['-I', '-u', 'workers/onnx_worker.py', badModel, String(process.pid)], { cwd: root, timeout: 20000 });
  if (bad.status === 0 || bad.stdout.includes(Buffer.from('COGW1'))) throw new Error('unapproved model accepted');
  daemon = spawn(path.join(root, 'target/debug/ecosd'), ['--socket', socket,
    '--worker-python', python, '--worker-script', path.join(root, 'workers/onnx_worker.py'), '--mnist-model', model]);
  exited = new Promise(resolve => { daemon.once('exit', (code, signal) => resolve({ code, signal })); daemon.once('error', error => resolve({ error })); });
  await new Promise((resolve, reject) => {
    let output = '';
    daemon.stdout.on('data', data => { output += data; if (output.includes('READY ')) resolve(); });
    daemon.stderr.on('data', data => process.stderr.write(data));
    daemon.once('error', reject);
    daemon.once('exit', () => reject(new Error('daemon exited before ready')));
    timer = setTimeout(() => reject(new Error('startup timeout')), 5000);
  });
  clearTimeout(timer);
  for (const sample of oracle) {
    const actual = JSON.parse(run(path.join(root, 'target/debug/cog'), ['--socket', socket, 'digit', sample.path]));
    if (actual.digit !== sample.digit) throw new Error(`reference mismatch: ${actual.digit} != ${sample.digit}`);
  }
  const cTest = path.join(scratch, 'c-onnx');
  const lib = path.join(root, 'target/debug');
  run('cc', ['-std=c11', '-Wall', '-Wextra', '-Werror', '-Iinclude', 'tests/c-onnx.c',
    '-L', lib, '-lcogposix', `-Wl,-rpath,${lib}`, '-o', cTest]);
  process.stdout.write(run(cTest, [socket, oracle[0].path, String(oracle[0].digit)]));
  function workerPid() {
    const children = readdirSync(`/proc/${daemon.pid}/task`).flatMap(tid => {
      try { return readFileSync(`/proc/${daemon.pid}/task/${tid}/children`, 'utf8').trim().split(/\s+/).filter(Boolean); }
      catch { return []; }
    });
    if (children.length !== 1) throw new Error(`expected one worker, got ${children.length}`);
    return Number(children[0]);
  }
  const oldPid = workerPid();
  process.kill(oldPid, 'SIGKILL');
  const deadline = Date.now() + 2000;
  while (Date.now() < deadline) {
    try { if (readFileSync(`/proc/${oldPid}/stat`, 'utf8').split(') ')[1].startsWith('Z')) break; }
    catch { break; }
    await new Promise(resolve => setTimeout(resolve, 2));
  }
  const failed = spawnSync(path.join(root, 'target/debug/cog'), ['--socket', socket, 'digit', oracle[0].path], { encoding: 'utf8', timeout: 20000 });
  if (failed.error || failed.status === 0) throw new Error('dead worker did not fail the affected request');
  const recovered = JSON.parse(run(path.join(root, 'target/debug/cog'), ['--socket', socket, 'digit', oracle[0].path]));
  if (recovered.digit !== oracle[0].digit || workerPid() === oldPid) throw new Error('worker recovery failed');
  daemon.kill('SIGTERM');
  const result = await Promise.race([exited, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('shutdown timeout')), 5000); })]);
  if (result.code !== 0) throw new Error(`daemon shutdown failed: ${JSON.stringify(result)}`);
  console.log(`ONNX CPU: ${oracle.length} classifications match the independent ONNX reference evaluator; C/shared input, real worker crash/recovery, sandbox probes and artifact rejection passed.`);
} catch (error) {
  console.error(error.message); process.exitCode = 1;
} finally {
  clearTimeout(timer);
  if (daemon && daemon.exitCode === null && daemon.signalCode === null) daemon.kill('SIGKILL');
  await exited;
  rmSync(scratch, { recursive: true, force: true });
}
