// Schema/semantic regression tests. Synthetic values are not measured AI output.
// Rights and component licenses: component LICENSE.md.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { CAPABILITY,QUERY_SCHEMA,REQUEST_SCHEMA,RESULT_SCHEMA,EXCHANGE,CapabilityError,
  mapRequest,mapResult,mapFailure,makeReceipt,digest,canonical } from './adapter.mjs';

const snap='sha256:'+'a'.repeat(64);
const grant={authorized:true,subject:'test-user',snapshot_digest:snap};
const facts={executor:'schema-validation-test',deployment_digest:'sha256:'+'b'.repeat(64),
  locality:'endpoint',device:'cpu',network_used:false,started_at:'2026-10-22T00:00:00Z',
  finished_at:'2026-10-22T00:00:00.010Z',duration_ms:10,cache:'not-applicable'};

function request(changes={}) {
  const value={query:'How does local retrieval work?',candidate_count:20,top_k:5,rerank:true,...changes};
  return {contract:EXCHANGE,request_id:'request.test.0001',idempotency_key:'workbook.test.cellA1.rev1',capability:CAPABILITY,
    inputs:[{name:'query',location:'inline',media_type:'application/json',schema:QUERY_SCHEMA,digest:digest(value),value},
      {name:'corpus',location:'artifact',media_type:'application/json',schema:'etex.corpus.snapshot.v1',digest:snap,artifact_ref:snap}],
    output_schema:RESULT_SCHEMA,placement:'local-required',constraints:{network:'deny',timeout_ms:5000,max_output_bytes:65532}};
}
function denied(fn,code) { assert.throws(fn,e=>e instanceof CapabilityError && e.code===code); }
function rejected(fn) { assert.throws(fn,e=>e instanceof CapabilityError); }
function remapQuery(r,changes) { Object.assign(r.inputs[0].value,changes);r.inputs[0].digest=digest(r.inputs[0].value);return r; }

// A complete artificial object with the REAL worker's shape. This is deliberately
// constructed test data, not a claimed inference result or a corpus/quality test.
function realShape(r=request(),effective=r.inputs[0].value.candidate_count) {
  const q=r.inputs[0].value,count=Math.min(effective,q.top_k);
  return {schema:RESULT_SCHEMA,request_id:r.request_id,status:'completed',cog_status:0,engine:'etex',error:null,
    results:Array.from({length:count},(_,i)=>({document_id:'synthetic-schema-row-'+i,row_id:i,
      title:'Synthetic validation data',snippet:'This row was constructed only to test schema validation.',
      title_truncated:false,snippet_truncated:false,dense_score:0.8-i*0.05,reranker_logit:q.rerank ? 1.0-i*0.1 : null})),
    provenance:{snapshot_sha256:'a'.repeat(64),index_manifest_sha256:'b'.repeat(64),source_sha256:'c'.repeat(64),
      encoder_model_sha256:'d'.repeat(64),encoder_tokenizer_sha256:'e'.repeat(64),
      reranker_model_sha256:q.rerank ? 'f'.repeat(64) : null,reranker_tokenizer_sha256:q.rerank ? '0'.repeat(64) : null,
      worker_version:'1.0.0',fixture:false,native_backend:'synthetic-schema-validation',
      model_quantization_note:'Synthetic validation object; no model execution is claimed by this test.'},
    execution:{candidate_count_requested:q.candidate_count,candidate_count_effective:effective,top_k_requested:q.top_k,
      returned_count:count,rerank_requested:q.rerank,rerank_performed:q.rerank,strategy:'bounded',index_layout:'packed',
      dense_exactness:{certified:true,reference:'fp32-products-fp64-eight-lanes-no-fma-v1',
        scope:'Synthetic schema-validation object, not measured inference evidence.',source_verified:true,
        excluded_count:80,refined_count:Math.max(effective,20),fallback:false},
      reranking_scope:q.rerank ? `Only the exact dense top-${effective} candidates; not global cross-encoder top-k.` : 'Reranking not performed.'},
    timings_ms:{encoder:1,dense_search:2,documents_and_reranking:3,total:6}};
}

test('maps an authorized request using a distinct semantic query schema',()=>{
  const m=mapRequest(request(),grant);
  assert.equal(m.worker.schema,REQUEST_SCHEMA);assert.notEqual(QUERY_SCHEMA,REQUEST_SCHEMA);
  assert.equal(m.worker.top_k,5);assert.equal(m.worker.query,'How does local retrieval work?');
  for (const key of ['corpus','tenant','snapshot','transport_only']) assert.equal(Object.hasOwn(m.worker,key),false);
  assert.equal(m.transport_only,false);assert.ok(Object.isFrozen(m.worker));
});
test('requires a trusted authenticated corpus grant',()=>{
  for (const bad of [{...grant,authorized:false},{...grant,subject:''},{...grant,subject:12},
    {...grant,snapshot_digest:'bad'},{...grant,snapshot_digest:snap+'\n'},{...grant,transport_only:'true'}])
    denied(()=>mapRequest(request(),bad),'policy_denied');
});
test('rejects another snapshot and forged query digest',()=>{
  const r=request();r.inputs[1].artifact_ref='sha256:'+'c'.repeat(64);denied(()=>mapRequest(r,grant),'policy_denied');
  const q=request();q.inputs[0].value.query='changed';denied(()=>mapRequest(q,grant),'schema_mismatch');
});
test('the partial semantic query cannot claim the full worker request schema',()=>{
  const r=request();r.inputs[0].schema=REQUEST_SCHEMA;denied(()=>mapRequest(r,grant),'schema_mismatch');
});
test('rejects remote placement and network-enabled policy',()=>{
  const r=request();r.placement='approved-node-allowed';denied(()=>mapRequest(r,grant),'unsupported');
  r.placement='local-required';r.constraints.network='approved-external-only';denied(()=>mapRequest(r,grant),'policy_denied');
});
test('does not infer a qualified deterministic deployment from a request',()=>{
  const r=request();r.constraints.deterministic_required=true;
  denied(()=>mapRequest(r,grant),'unsupported');
  assert.equal(mapRequest(r,{...grant,deterministic_qualified:true}).requirements.deterministic_required,true);
});
test('rejects deadlines and capacities outside the semantic profile',()=>{
  for (const values of [{max_output_bytes:65536},{max_output_bytes:0},{timeout_ms:60001},{timeout_ms:0},{timeout_ms:true}]) {
    const r=request();Object.assign(r.constraints,values);denied(()=>mapRequest(r,grant),'invalid_input');
  }
});
test('rejects duplicate inputs, extra authority fields and unknown properties',()=>{
  const r=request();r.inputs[1]=structuredClone(r.inputs[0]);denied(()=>mapRequest(r,grant),'invalid_input');
  for (const key of ['path','snapshot','tenant','engine','transport_only']) {
    const q=remapQuery(request(),{[key]:'forbidden'});denied(()=>mapRequest(q,grant),'schema_mismatch');
  }
});
test('request IDs satisfy both upstream and worker contracts without coercion',()=>{
  for (const value of [12345678,true,null,'short','x'.repeat(65),'_request1','.request1','request1\n']) {
    const r=request();r.request_id=value;denied(()=>mapRequest(r,grant),'invalid_input');
  }
  for (const value of ['a'.repeat(8),'Z'.repeat(64),'request:valid_1']) {
    const r=request();r.request_id=value;assert.equal(mapRequest(r,grant).worker.request_id,value);
  }
});
test('idempotency IDs retain upstream range but require actual strings',()=>{
  for (const value of [12345678,'short','x'.repeat(129),'abcdefgh\n']) {
    const r=request();r.idempotency_key=value;denied(()=>mapRequest(r,grant),'invalid_input');
  }
  const r=request();r.idempotency_key='.'.repeat(128);assert.equal(mapRequest(r,grant).idempotency_key,r.idempotency_key);
});
test('rejects C0 controls and permits only worker-supported whitespace controls',()=>{
  for (let c=0;c<32;c++) if (![9,10,13].includes(c))
    denied(()=>mapRequest(request({query:'text'+String.fromCharCode(c)}),grant),'invalid_input');
  assert.equal(mapRequest(request({query:'line one\nline two\ttab\rreturn'}),grant).worker.query,'line one\nline two\ttab\rreturn');
});
test('query byte budget and encoded frame budget are distinct',()=>{
  denied(()=>mapRequest(request({query:'界'.repeat(1001)}),grant),'invalid_input');
  assert.equal(mapRequest(request({query:'界'.repeat(1000)}),grant).worker.query.length,1000);
  denied(()=>mapRequest(request({query:'\t'.repeat(2999)+'x'}),grant),'invalid_input');
});
test('optional application presence and upstream types are checked',()=>{
  for (const app of [null,false,0,'',{}, {id:'a.v1',version:1},{id:'a'.repeat(129)+'.v1',version:'1'},
    {id:'a.v1',version:'1',workload_ref:12345678},{id:'a.v1\n',version:'1'}]) {
    const r=request();r.application=app;rejected(()=>mapRequest(r,grant));
  }
  const r=request();r.application={id:'logforce.cogsheet.v1',version:'test',workload_ref:'workbook.test.001'};
  assert.equal(mapRequest(r,grant).worker.schema,REQUEST_SCHEMA);
});
test('canonical JSON rejects nonfinite values, malformed Unicode and non-JSON structures',()=>{
  for (const value of ['\ud800',{a:NaN},[Infinity],new Date(),Array(2)]) denied(()=>canonical(value),'invalid_input');
  const cycle={};cycle.self=cycle;denied(()=>canonical(cycle),'invalid_input');
  assert.equal(canonical({b:1,a:'ok'}),'{"a":"ok","b":1}');
});
test('commits a complete real-mode shape and preserves separate score meanings',()=>{
  const r=request(),m=mapRequest(r,grant),value=realShape(r),result=mapResult(m,value,facts);
  assert.equal(result.status,'complete');assert.equal(result.output.digest,digest(value));
  assert.equal(result.receipt.capability,CAPABILITY);assert.deepEqual(result.output.value,value);
  assert.notEqual(value.results[0].dense_score,value.results[0].reranker_logit);
});
test('schema-valid worker status must explicitly indicate success',()=>{
  const r=request(),m=mapRequest(r,grant);
  for (const mutate of [v=>delete v.status,v=>delete v.cog_status,v=>delete v.error,
    v=>v.status='failed',v=>v.cog_status=8,v=>v.cog_status=false,v=>v.error={code:'internal'},
    v=>v.results=[],v=>v.engine='unknown',v=>v.extra='forbidden']) {
    const value=realShape(r);mutate(value);rejected(()=>mapResult(m,value,facts));
  }
});
test('old incomplete test-shaped results are no longer accepted',()=>{
  const m=mapRequest(request(),grant);
  for (const value of [{schema:RESULT_SCHEMA,request_id:m.request_id,results:[]},
    {schema:RESULT_SCHEMA,request_id:m.request_id,status:'completed',results:[],provenance:{fixture:true}}])
    rejected(()=>mapResult(m,value,facts));
});
test('real output is bound to its approved snapshot, not only to request ID',()=>{
  const r=request(),m=mapRequest(r,grant),value=realShape(r);
  value.provenance.snapshot_sha256='f'.repeat(64);denied(()=>mapResult(m,value,facts),'policy_denied');
  value.provenance.snapshot_sha256='a'.repeat(64);value.request_id='request.other.001';
  denied(()=>mapResult(m,value,facts),'schema_mismatch');
});
test('fixture output requires a trusted explicit transport-only designation',()=>{
  const fixture=JSON.parse(readFileSync(new URL('../integration/examples/fixture_result.json',import.meta.url),'utf8'));
  const r=request({candidate_count:3,top_k:2,rerank:false});fixture.request_id=r.request_id;
  denied(()=>mapResult(mapRequest(r,grant),fixture,facts),'policy_denied');
  const m=mapRequest(r,{...grant,transport_only:true}),out=mapResult(m,fixture,facts);
  assert.equal(out.output.value.engine,'fixture');assert.equal(out.output.value.provenance.fixture,true);
  assert.equal(out.output.value.execution.dense_exactness.certified,false);
  denied(()=>mapResult(m,realShape(r),facts),'policy_denied');
});
test('transport fixture cannot add model hashes or dense certification',()=>{
  const base=JSON.parse(readFileSync(new URL('../integration/examples/fixture_result.json',import.meta.url),'utf8'));
  const r=request({candidate_count:3,top_k:2,rerank:false}),m=mapRequest(r,{...grant,transport_only:true});base.request_id=r.request_id;
  for (const mutate of [v=>v.execution.dense_exactness.certified=true,
    v=>v.provenance.encoder_model_sha256='a'.repeat(64),v=>v.provenance.fixture=false,
    v=>v.execution.strategy='bounded',v=>v.timings_ms={total:1}]) {
    const v=structuredClone(base);mutate(v);rejected(()=>mapResult(m,v,facts));
  }
});
test('metadata counts and requested limits must match the actual request and result',()=>{
  const r=request(),m=mapRequest(r,grant);
  for (const mutate of [v=>v.execution.candidate_count_requested=19,v=>v.execution.top_k_requested=4,
    v=>v.execution.candidate_count_effective=21,v=>v.execution.returned_count=4,
    v=>v.results.pop(),v=>v.execution.dense_exactness.refined_count=1,
    v=>v.execution.candidate_count_effective=true]) {
    const v=realShape(r);mutate(v);rejected(()=>mapResult(m,v,facts));
  }
  const small=realShape(r,3);assert.equal(mapResult(m,small,facts).output.value.results.length,3);
});
test('rerank metadata and nullable logits cannot be silently substituted',()=>{
  const r=request(),m=mapRequest(r,grant);
  for (const mutate of [v=>v.execution.rerank_performed=false,v=>v.execution.rerank_requested=false,
    v=>v.results[0].reranker_logit=null,v=>v.provenance.reranker_model_sha256=null]) {
    const v=realShape(r);mutate(v);rejected(()=>mapResult(m,v,facts));
  }
  const no=request({rerank:false}),value=realShape(no);assert.equal(mapResult(mapRequest(no,grant),value,facts).status,'complete');
  value.results[0].reranker_logit=0;rejected(()=>mapResult(mapRequest(no,grant),value,facts));
});
test('row types, byte caps, finite scores and duplicate row IDs are enforced',()=>{
  const r=request(),m=mapRequest(r,grant);
  for (const mutate of [v=>v.results[1].row_id=0,v=>v.results[0].row_id=2**53,
    v=>v.results[0].row_id=true,v=>v.results[0].dense_score=Infinity,v=>v.results[0].reranker_logit=NaN,
    v=>v.results[0].title='界'.repeat(86),v=>v.results[0].snippet='x'.repeat(1537),
    v=>v.results[0].document_id='x'.repeat(513),v=>v.results[0].snippet_truncated=0,
    v=>delete v.results[0].title_truncated,v=>v.results[0].extra=1]) {
    const v=realShape(r);mutate(v);rejected(()=>mapResult(m,v,facts));
  }
});
test('provenance and exactness metadata cannot omit hashes or invent mixed-v3 support',()=>{
  const r=request(),m=mapRequest(r,grant);
  for (const mutate of [v=>delete v.provenance.source_sha256,v=>v.provenance.source_sha256=null,
    v=>v.provenance.source_sha256='c'.repeat(64)+'\n',v=>v.provenance.worker_version='2.0',
    v=>v.execution.index_layout='mixed',v=>v.execution.dense_exactness.certified=false,
    v=>v.execution.dense_exactness.source_verified=false,v=>v.execution.dense_exactness.reference='blas-fp32',
    v=>v.execution.dense_exactness.extra=1,v=>v.timings_ms.total=NaN,v=>v.timings_ms.encoder=-1,
    v=>delete v.timings_ms.dense_search]) {
    const v=realShape(r);mutate(v);rejected(()=>mapResult(m,v,facts));
  }
});
test('canonical output budget is enforced after full semantic validation',()=>{
  const r=request(),value=realShape(r),size=Buffer.byteLength(canonical(value),'utf8');
  r.constraints.max_output_bytes=size;assert.equal(mapResult(mapRequest(r,grant),value,facts).status,'complete');
  r.constraints.max_output_bytes=size-1;denied(()=>mapResult(mapRequest(r,grant),value,facts),'invalid_input');
});
test('receipt requires typed valid UTC RFC3339 calendar timestamps',()=>{
  const m=mapRequest(request(),grant);
  for (const bad of [0,null,'2026-10-22','10/22/2026','2026-02-30T00:00:00Z','2026-10-22T24:00:00Z',
    '2026-10-22T00:00:00+00:00','2026-10-22T00:00:00Z\n','2026-10-22T00:00:00.0001Z'])
    denied(()=>makeReceipt(m,{...facts,started_at:bad}),'invalid_input');
  for (const fraction of ['','.1','.12','.123']) {
    const instant='2026-10-22T00:00:00'+fraction+'Z';
    assert.equal(makeReceipt(m,{...facts,started_at:instant,finished_at:instant,duration_ms:0}).started_at,instant);
  }
  denied(()=>makeReceipt(m,{...facts,started_at:'2026-10-23T00:00:00Z'}),'invalid_input');
});
test('receipt locality/network and request elapsed budget cannot become success',()=>{
  const r=request(),m=mapRequest(r,grant),value=realShape(r);
  for (const change of [{network_used:true},{device:'gpu'},{locality:'external-service'}])
    denied(()=>mapResult(m,value,{...facts,...change}),'policy_denied');
  denied(()=>mapResult(m,value,{...facts,duration_ms:5001}),'timeout');
  denied(()=>makeReceipt(m,{...facts,duration_ms:true}),'invalid_input');
  denied(()=>makeReceipt(m,{...facts,started_at:'2026-10-22T00:00:00Z',finished_at:'2026-10-21T00:00:00Z'}),'invalid_input');
});
test('committed output does not alias the mutable worker object',()=>{
  const r=request(),value=realShape(r),out=mapResult(mapRequest(r,grant),value,facts);
  value.results[0].snippet='changed after commit';value.provenance.snapshot_sha256='f'.repeat(64);
  assert.notEqual(out.output.value.results[0].snippet,value.results[0].snippet);
  assert.equal(out.output.digest,digest(out.output.value));
});
test('terminal native errors map to typed failures and never include output',()=>{
  const m=mapRequest(request(),grant),r=mapFailure(m,6,facts);
  assert.equal(r.status,'cancelled');assert.equal(r.error.retryable,false);assert.equal(Object.hasOwn(r,'output'),false);
  assert.equal(mapFailure(m,7,facts).status,'blocked');assert.equal(mapFailure(m,5,facts).error.code,'timeout');
  for (const bad of [0,12,true,'6',null]) denied(()=>mapFailure(m,bad,facts),'invalid_input');
});
test('checked-in semantic examples have valid digests and map to the worker schema',()=>{
  const r=JSON.parse(readFileSync(new URL('./examples/document_search_request.json',import.meta.url),'utf8'));
  const trusted=JSON.parse(readFileSync(new URL('./examples/trusted_context.example.json',import.meta.url),'utf8'));
  assert.equal(mapRequest(r,trusted).worker.schema,REQUEST_SCHEMA);
  assert.equal(r.inputs[0].schema,QUERY_SCHEMA);
});
test('accepts recorded real C ABI and direct-worker outputs without rerunning inference',()=>{
  const report=JSON.parse(readFileSync(new URL('../validation/ecos/c_abi_e2e.json',import.meta.url),'utf8'));
  assert.equal(report.status,'pass');assert.equal(report.direct_worker_comparison.passed,true);
  const real=report.engines.find(engine=>engine.engine==='etex');
  const outputs=[...real.calls.filter(call=>call.passed && call.result).map(call=>call.result),
    report.direct_worker_comparison.result];
  assert.equal(outputs.length,3);
  for (const value of outputs) {
    const q=report.direct_worker_comparison.request;
    const r=request({query:q.query,candidate_count:q.candidate_count,top_k:q.top_k,rerank:q.rerank});
    r.request_id=value.request_id;
    const snapshot='sha256:'+value.provenance.snapshot_sha256;
    r.inputs[1].digest=r.inputs[1].artifact_ref=snapshot;
    const m=mapRequest(r,{...grant,snapshot_digest:snapshot});
    // These are synthetic schema-test receipt facts, not a newly measured receipt.
    // Only the mapping of the already recorded real output is exercised here.
    const out=mapResult(m,value,facts);
    assert.equal(out.status,'complete');assert.deepEqual(out.output.value,value);
    assert.equal(out.output.digest,digest(value));
  }
});
