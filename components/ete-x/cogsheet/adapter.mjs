/** ETE-X integration. Rights and upstream licenses: component LICENSE.md.
 * Pure semantic adapter for a trusted local bridge. This file does not authenticate
 * users, enforce an OS sandbox, open sockets, fetch artifacts, or execute a job.
 */
import { createHash } from 'node:crypto';

export const CAPABILITY = 'etex.rag.search.v1';
export const QUERY_SCHEMA = 'etex.rag.query.v1';
export const REQUEST_SCHEMA = 'etex.rag.search.request.v1';
export const RESULT_SCHEMA = 'etex.rag.search.result.v1';
export const EXCHANGE = 'ecos-capability-exchange.v1';
const ID = /^[A-Za-z0-9._:-]{8,128}$/;
const WORKER_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$/;
const DIGEST = /^sha256:[0-9a-f]{64}$/;
const HASH = /^[0-9a-f]{64}$/;
const PROFILE = /^[a-z][a-z0-9_-]*(?:\.[a-z0-9_-]+)*\.v[1-9][0-9]*$/;
const REFERENCE = 'fp32-products-fp64-eight-lanes-no-fma-v1';
const HASH_FIELDS = ['snapshot_sha256','index_manifest_sha256','source_sha256','encoder_model_sha256',
  'encoder_tokenizer_sha256','reranker_model_sha256','reranker_tokenizer_sha256'];

export class CapabilityError extends Error {
  constructor(code, message) { super(message); this.name = 'CapabilityError'; this.code = code; }
}
function fail(code, message) { throw new CapabilityError(code, message); }
function object(value, keys, required = keys) {
  if (!value || typeof value !== 'object' || Object.getPrototypeOf(value)!==Object.prototype)
    fail('invalid_input', 'Expected a plain JSON object.');
  if (Object.keys(value).some(k => !keys.includes(k)) || required.some(k => !Object.hasOwn(value,k)))
    fail('schema_mismatch', 'Unexpected or missing field.');
}
function integer(v, low, high) { return Number.isSafeInteger(v) && v >= low && v <= high; }
// JS '$' alone also matches before a final newline. Demand a whole-string match.
function matches(value, pattern) { return typeof value==='string' && pattern.exec(value)?.[0]===value; }
function text(value, maxBytes, nonempty=false) {
  return typeof value==='string' && value.isWellFormed() && Buffer.byteLength(value,'utf8')<=maxBytes
    && (!nonempty || value.trim().length>0);
}
function invalidResult() { fail('schema_mismatch','Worker result violates the successful result contract.'); }
function finite(value) { return typeof value==='number' && Number.isFinite(value); }

/** Canonical JSON for this profile's limited JSON values; not a claim of RFC 8785. */
export function canonical(value, depth=0) {
  if (depth>12) fail('invalid_input','JSON nesting exceeds this profile.');
  if (value === null || typeof value === 'boolean') return JSON.stringify(value);
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) fail('invalid_input', 'Nonfinite number.');
    return JSON.stringify(value);
  }
  if (typeof value === 'string') {
    if (!value.isWellFormed()) fail('invalid_input', 'Ill formed Unicode string.');
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    for (let i=0;i<value.length;i++) if (!Object.hasOwn(value,i)) fail('invalid_input','Sparse arrays are not JSON.');
    return '[' + value.map(item=>canonical(item,depth+1)).join(',') + ']';
  }
  if (value && typeof value === 'object' && Object.getPrototypeOf(value) === Object.prototype)
    return '{' + Object.keys(value).sort().map(k => canonical(k,depth+1) + ':' + canonical(value[k],depth+1)).join(',') + '}';
  fail('invalid_input', 'Unsupported JSON value.');
}
export function digest(value) { return 'sha256:' + createHash('sha256').update(canonical(value),'utf8').digest('hex'); }

/** approved is supplied by an authenticated trusted host, never by a workbook. */
export function mapRequest(request, approved) {
  object(request,['contract','request_id','idempotency_key','capability','inputs','output_schema','placement','constraints','application'],
    ['contract','request_id','idempotency_key','capability','inputs','output_schema','placement','constraints']);
  if (request.contract !== EXCHANGE || request.capability !== CAPABILITY || request.output_schema !== RESULT_SCHEMA)
    fail('unsupported','Unsupported capability or contract version.');
  if (!matches(request.request_id,WORKER_ID) || !matches(request.idempotency_key,ID)) fail('invalid_input','Invalid request identity.');
  if (!approved || approved.authorized !== true || !text(approved.subject,256,true))
    fail('policy_denied','An authenticated corpus grant is required.');
  if (!matches(approved.snapshot_digest,DIGEST)) fail('policy_denied','An approved snapshot identity is required.');
  if (Object.hasOwn(approved,'transport_only') && typeof approved.transport_only!=='boolean')
    fail('policy_denied','The transport-only designation must come from a typed trusted context.');
  if (request.placement !== 'local-required') fail('unsupported','This adapter supports endpoint local execution only.');
  const c=request.constraints;
  object(c,['network','timeout_ms','max_output_bytes','deterministic_required'],['network','timeout_ms','max_output_bytes']);
  if (c.network !== 'deny') fail('policy_denied','This profile requires network deny.');
  if (!integer(c.timeout_ms,1,60000) || !integer(c.max_output_bytes,1,65532))
    fail('invalid_input','Unsupported deadline or output capacity.');
  if (Object.hasOwn(c,'deterministic_required') && typeof c.deterministic_required !== 'boolean')
    fail('invalid_input','Invalid determinism constraint.');
  if (c.deterministic_required === true && approved.deterministic_qualified !== true)
    fail('unsupported','This deployment has no qualified deterministic profile.');
  if (Object.hasOwn(request,'application')) {
    object(request.application,['id','version','workload_ref'],['id','version']);
    if (!text(request.application.id,128,true) || !matches(request.application.id,PROFILE)
        || !text(request.application.version,64,true)
        || (Object.hasOwn(request.application,'workload_ref') && !matches(request.application.workload_ref,ID)))
      fail('invalid_input','Invalid application metadata.');
  }
  if (!Array.isArray(request.inputs) || request.inputs.length !== 2) fail('invalid_input','Exactly query and corpus inputs are required.');
  const byName = new Map();
  for (const input of request.inputs) {
    if (!input || byName.has(input.name)) fail('invalid_input','Duplicate or invalid input.');
    byName.set(input.name,input);
  }
  const q=byName.get('query'), corpus=byName.get('corpus');
  object(q,['name','location','media_type','schema','digest','value']);
  object(corpus,['name','location','media_type','schema','digest','artifact_ref']);
  if (corpus.location !== 'artifact' || corpus.media_type !== 'application/json' || corpus.schema !== 'etex.corpus.snapshot.v1'
      || corpus.digest !== approved.snapshot_digest || corpus.artifact_ref !== approved.snapshot_digest)
    fail('policy_denied','Corpus identity is outside the approved grant.');
  const value=q.value;
  object(value,['query','candidate_count','top_k','rerank']);
  if (!text(value.query,3000,true) || /[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(value.query))
    fail('invalid_input','Invalid query length.');
  if (!integer(value.candidate_count,1,100) || !integer(value.top_k,1,20) || value.top_k>value.candidate_count || typeof value.rerank!=='boolean')
    fail('invalid_input','Invalid search limits.');
  canonical(value);
  if (q.location !== 'inline' || q.media_type !== 'application/json' || q.schema !== QUERY_SCHEMA || q.digest !== digest(value))
    fail('schema_mismatch','Query identity or schema does not match.');
  const worker={schema:REQUEST_SCHEMA,request_id:request.request_id,...value};
  if (Buffer.byteLength(JSON.stringify(worker),'utf8')>4092) fail('invalid_input','Worker request capacity exceeded.');
  return Object.freeze({worker:Object.freeze(worker),request_id:request.request_id,idempotency_key:request.idempotency_key,
    snapshot_digest:approved.snapshot_digest,transport_only:approved.transport_only===true,
    requirements:Object.freeze({...c,placement:'local-required'})});
}

// This profile accepts the UTC subset of RFC 3339, with 0..3 fractional digits.
// Matching the normalized calendar value also rejects rollover dates such as Feb 30.
function utcTimestamp(value) {
  const pattern=/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$/;
  if (!matches(value,pattern)) fail('invalid_input','Receipt timestamps must use the supported UTC RFC3339 profile.');
  const parsed=Date.parse(value);
  const fraction=value.includes('.') ? value.slice(20,-1).padEnd(3,'0') : '000';
  if (!Number.isFinite(parsed) || new Date(parsed).toISOString()!==value.slice(0,19)+'.'+fraction+'Z')
    fail('invalid_input','Invalid receipt calendar timestamp.');
  return parsed;
}

/** Facts must come from the executor. A caller claim of local-required is not proof. */
export function makeReceipt(mapped, facts) {
  object(facts,['executor','deployment_digest','package_digest','locality','device','network_used','started_at','finished_at','duration_ms','cache'],
    ['executor','deployment_digest','locality','device','network_used','started_at','finished_at','duration_ms','cache']);
  if (!text(facts.executor,128,true) || facts.executor.length<3 || !matches(facts.deployment_digest,DIGEST)
      || (Object.hasOwn(facts,'package_digest') && !matches(facts.package_digest,DIGEST))) fail('invalid_input','Invalid execution identity.');
  if (facts.locality!=='endpoint' || facts.device!=='cpu' || facts.network_used!==false)
    fail('policy_denied','Executor did not confirm the required local boundary.');
  const start=utcTimestamp(facts.started_at), finish=utcTimestamp(facts.finished_at);
  if (finish<start || !integer(facts.duration_ms,0,3600000)
      || !['hit','miss','not-applicable'].includes(facts.cache)) fail('invalid_input','Invalid execution measurements.');
  return {capability:CAPABILITY,...facts};
}

/** Exact successful worker object, plus this semantic profile's trusted binding. */
export function validateWorkerSuccess(mapped,result) {
  object(result,['schema','request_id','status','cog_status','engine','results','error','provenance','execution','timings_ms']);
  if (result.schema!==RESULT_SCHEMA || result.request_id!==mapped.request_id || !matches(result.request_id,WORKER_ID)
      || result.status!=='completed' || result.cog_status!==0 || result.error!==null
      || !['etex','fixture'].includes(result.engine) || !Array.isArray(result.results)
      || result.results.length<1 || result.results.length>20) invalidResult();
  const seen=new Set();
  for (const row of result.results) {
    object(row,['document_id','row_id','title','snippet','title_truncated','snippet_truncated','dense_score','reranker_logit']);
    if (!text(row.document_id,512,true) || !integer(row.row_id,0,Number.MAX_SAFE_INTEGER) || seen.has(row.row_id)
        || !text(row.title,256) || !text(row.snippet,1536) || typeof row.title_truncated!=='boolean'
        || typeof row.snippet_truncated!=='boolean' || !finite(row.dense_score)
        || (row.reranker_logit!==null && !finite(row.reranker_logit))) invalidResult();
    seen.add(row.row_id);
  }
  const e=result.execution;
  object(e,['candidate_count_requested','candidate_count_effective','top_k_requested','returned_count',
    'rerank_requested','rerank_performed','strategy','index_layout','dense_exactness','reranking_scope']);
  if (e.candidate_count_requested!==mapped.worker.candidate_count || e.top_k_requested!==mapped.worker.top_k
      || e.rerank_requested!==mapped.worker.rerank || typeof e.rerank_performed!=='boolean'
      || e.rerank_performed!==e.rerank_requested
      || !integer(e.candidate_count_effective,1,e.candidate_count_requested)
      || !integer(e.returned_count,1,20) || e.returned_count!==result.results.length
      || e.returned_count!==Math.min(e.candidate_count_effective,e.top_k_requested)
      || !text(e.reranking_scope,320,true)
      || result.results.some(row=>(row.reranker_logit!==null)!==e.rerank_performed)) invalidResult();
  const exact=e.dense_exactness;
  object(exact,['certified','reference','scope','source_verified','excluded_count','refined_count','fallback']);
  if (typeof exact.certified!=='boolean' || typeof exact.source_verified!=='boolean' || typeof exact.fallback!=='boolean'
      || !text(exact.scope,600,true) || (exact.reference!==null && !text(exact.reference,128,true))) invalidResult();
  for (const key of ['excluded_count','refined_count'])
    if (exact[key]!==null && !integer(exact[key],0,Number.MAX_SAFE_INTEGER)) invalidResult();
  const p=result.provenance;
  object(p,[...HASH_FIELDS,'worker_version','fixture','native_backend','model_quantization_note']);
  for (const key of HASH_FIELDS) if (p[key]!==null && !matches(p[key],HASH)) invalidResult();
  if (p.worker_version!=='1.0.0' || typeof p.fixture!=='boolean' || !text(p.model_quantization_note,320,true)
      || (p.native_backend!==null && !text(p.native_backend,128,true))) invalidResult();
  if (result.engine==='fixture') {
    if (mapped.transport_only!==true) fail('policy_denied','A transport fixture cannot be committed as a real search result.');
    if (!p.fixture || HASH_FIELDS.some(key=>p[key]!==null) || p.native_backend!==null
        || e.strategy!=='transport_fixture' || e.index_layout!=='fixture' || e.rerank_performed
        || exact.certified || exact.source_verified || exact.reference!==null
        || exact.excluded_count!==null || exact.refined_count!==null || exact.fallback
        || result.timings_ms!==null) invalidResult();
  } else {
    if (mapped.transport_only===true) fail('policy_denied','This request was admitted for transport-only testing.');
    if (p.fixture || HASH_FIELDS.slice(0,5).some(key=>p[key]===null)
        || !['raw','bounded'].includes(e.strategy) || !['rowwise','packed'].includes(e.index_layout)
        || !exact.certified || !exact.source_verified || exact.reference!==REFERENCE
        || (exact.refined_count!==null && exact.refined_count<e.candidate_count_effective)
        || (p.reranker_model_sha256===null)!==(p.reranker_tokenizer_sha256===null)
        || (e.rerank_performed && p.reranker_model_sha256===null)) invalidResult();
    if ('sha256:'+p.snapshot_sha256!==mapped.snapshot_digest)
      fail('policy_denied','Worker snapshot identity differs from the approved grant.');
    object(result.timings_ms,['encoder','dense_search','documents_and_reranking','total']);
    if (Object.values(result.timings_ms).some(value=>!finite(value)||value<0)) invalidResult();
  }
  return result;
}

export function mapResult(mapped, result, facts) {
  validateWorkerSuccess(mapped,result);
  const outputText=canonical(result);
  if (Buffer.byteLength(outputText,'utf8')>mapped.requirements.max_output_bytes)
    fail('invalid_input','Result exceeds the accepted output budget.');
  const receipt=makeReceipt(mapped,facts);
  if (facts.duration_ms>mapped.requirements.timeout_ms) fail('timeout','Execution exceeded the accepted deadline.');
  return {contract:EXCHANGE,request_id:mapped.request_id,status:'complete',
    output:{location:'inline',media_type:'application/json',schema:RESULT_SCHEMA,digest:digest(result),value:structuredClone(result)},receipt};
}

/** Use only after the native job is terminal. A timed-out wait does not cancel it. */
export function mapFailure(mapped, code, facts) {
  if (!integer(code,1,11)) fail('invalid_input','A nonzero terminal Cog execution error is required.');
  const errors={1:'invalid_input',2:'busy',3:'unavailable',4:'busy',5:'timeout',6:'cancelled',7:'policy_denied',8:'internal',9:'internal',10:'unsupported',11:'unavailable'};
  const category=errors[code]??'internal';
  return {contract:EXCHANGE,request_id:mapped.request_id,status:category==='cancelled'?'cancelled':category==='policy_denied'?'blocked':'failed',
    error:{code:category,message:'The local execution did not complete successfully.',retryable:false},receipt:makeReceipt(mapped,facts)};
}
