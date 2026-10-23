"""Local, immutable RAG retrieval prototype with encoder, mmap and reranker.

No external service is called. Exactness applies to dense top-k over the stored
FP32 vectors under the documented scorer, not to model quantization, relevance,
or a cross-encoder score for documents outside the retrieved candidate set.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import mmap
import os
from pathlib import Path
import shutil
import tempfile
import time
import numpy as np

from .bounded_search import BoundedIndex, build_index, stable_topk
from .bounded_search.native import exact_scores,backend as native_backend
from .bounded_search.packed_index import PackedBoundedIndex,build_packed_index
from .etex_mmap import MappedMatrix, build_store
from .nonar import MiniLMEncoder, MiniLMReranker
from .nonar.encoder import file_sha256

ROOT=Path(__file__).resolve().parent
DEFAULT_MODEL='model.fused.int8.full.onnx'

def encoder_identity(encoder):
    config_path=encoder.model_dir/'config.json'
    return {'model_sha256':file_sha256(encoder.model_path),
            'tokenizer_sha256':encoder.tokenizer_sha256,'max_length':encoder.max_length,
            'config_sha256':file_sha256(config_path) if config_path.is_file() else None,
            'pooling':'attention-mask mean, then FP32 L2 normalization',
            'document_batch_size':32,'sort_by_length':True,'query_batch_size':1,
            'dimension':384}

def _json_object(pairs):
    value={}
    for key,item in pairs:
        if key in value:raise ValueError(f'Duplicate snapshot field: {key}')
        value[key]=item
    return value

def _bad_constant(value):raise ValueError(f'Nonfinite snapshot number: {value}')

def _finite_float(value):
    result=float(value)
    if not math.isfinite(result):raise ValueError('Nonfinite snapshot number')
    return result

def _load_snapshot(folder):
    path=folder/'snapshot.json'
    if folder.is_symlink() or not folder.is_dir() or path.is_symlink() or not path.is_file() or path.stat().st_size>1024*1024:
        raise ValueError('Missing, symlinked or oversized snapshot manifest')
    manifest=json.loads(path.read_text(),object_pairs_hook=_json_object,
                        parse_constant=_bad_constant,parse_float=_finite_float)
    if not isinstance(manifest,dict) or manifest.get('format')!='ete-x-local-rag-snapshot-v1':
        raise ValueError('Unknown snapshot format')
    rows=manifest.get('rows');dimension=manifest.get('dimension')
    if (isinstance(rows,bool) or not isinstance(rows,int) or rows<1
        or isinstance(dimension,bool) or not isinstance(dimension,int) or dimension!=384):
        raise ValueError('Invalid snapshot dimensions')
    return manifest

class DocumentStore:
    """Read only the requested JSONL records, addressed by an mmap offset array."""
    def __init__(self,folder,rows):
        self.file=self.mapping=self.offsets=None
        try:
            self.file=(Path(folder)/'documents.jsonl').open('rb')
            self.mapping=mmap.mmap(self.file.fileno(),0,access=mmap.ACCESS_READ)
            self.offsets=np.load(Path(folder)/'document_offsets.npy',mmap_mode='r',allow_pickle=False)
            if (self.offsets.dtype!=np.dtype('<u8') or self.offsets.shape!=(rows+1,)
                or int(self.offsets[0])!=0 or int(self.offsets[-1])!=len(self.mapping)
                or np.any(self.offsets[1:]<=self.offsets[:-1])):
                raise ValueError('Invalid document offsets')
        except BaseException:self.close();raise
    def take(self,ids):
        result=[]
        for value in ids:
            row=int(value)
            if row<0 or row>=len(self.offsets)-1:raise IndexError(row)
            start,stop=map(int,self.offsets[row:row+2])
            result.append(json.loads(self.mapping[start:stop]))
        return result
    def close(self):
        offsets=getattr(self,'offsets',None)
        if offsets is not None and getattr(offsets,'_mmap',None) is not None:offsets._mmap.close()
        if getattr(self,'mapping',None) is not None:self.mapping.close()
        if getattr(self,'file',None) is not None:self.file.close()
        self.file=self.mapping=self.offsets=None

def build_snapshot(corpus_path,destination,encoder,*,compress=False,index_layout='auto'):
    """Build a reviewed immutable snapshot from local JSONL id/title/text rows.

    This convenience text builder holds the corpus texts while batching. For
    large precomputed vector corpora, use bounded_search.build_index directly.
    """
    destination=Path(destination).absolute()
    if index_layout not in {'auto','rowwise','packed'}:raise ValueError('Unknown index layout')
    if index_layout=='auto':
        index_layout='packed' if 'avx512_vnni' in native_backend() else 'rowwise'
    if destination.exists():raise FileExistsError(destination)
    records=[];seen=set()
    with Path(corpus_path).open(encoding='utf-8') as source:
        for line in source:
            if not line.strip():continue
            raw=json.loads(line)
            record={'id':raw.get('id',raw.get('_id')),'title':raw.get('title',''),'text':raw.get('text')}
            if any(not isinstance(record[key],str) for key in record) or not record['id'] or not record['text']:
                raise ValueError('Each document needs a nonempty string id/text and optional string title')
            if record['id'] in seen:raise ValueError('Document IDs must be unique')
            seen.add(record['id']);records.append(record)
    if not records:raise ValueError('Empty corpus')
    destination.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix=f'.{destination.name}.',dir=destination.parent))
    try:
        offsets=[0]
        with (stage/'documents.jsonl').open('wb') as output:
            for record in records:
                blob=(json.dumps(record,ensure_ascii=False,separators=(',',':'))+'\n').encode('utf-8')
                output.write(blob);offsets.append(offsets[-1]+len(blob))
        np.save(stage/'document_offsets.npy',np.asarray(offsets,dtype='<u8'))
        texts=[r['title']+' '+r['text'] for r in records]
        vectors=encoder.encode(texts,batch_size=32,sort_by_length=True)
        source_name='vectors.etm' if compress else 'vectors.npy'
        if compress:
            build_store(vectors,stage/source_name,group_rows=32,codec='zstd',shuffle='auto',level=3)
        else:np.save(stage/source_name,vectors)
        (build_packed_index if index_layout=='packed' else build_index)(vectors,stage/'index')
        manifest={'format':'ete-x-local-rag-snapshot-v1','rows':len(records),'dimension':384,
            'encoder':encoder_identity(encoder),'source_file':source_name,'index_layout':index_layout,
            'original_storage':'lossless_zstd_groups' if compress else 'raw_fp32_npy',
            'files':{name:{'sha256':file_sha256(stage/name),'bytes':(stage/name).stat().st_size}
                     for name in ['documents.jsonl','document_offsets.npy',source_name]},
            'contract':'Immutable snapshot; dense exactness is relative to stored FP32 vectors and the eight-lane FP64 scorer.',
            'build_runtime':{'versions':encoder.versions,'threads':encoder.num_threads},
            'model_quantization_note':'A quantized model can produce different embeddings from its FP32 source model.'}
        (stage/'snapshot.json').write_text(json.dumps(manifest,indent=2)+'\n')
        # Reserve exclusively; commit manifest last. Interrupted builds have no
        # valid manifest and cannot be opened as completed snapshots.
        destination.mkdir()
        for path in sorted(stage.iterdir(),key=lambda p:p.name=='snapshot.json'):
            path.rename(destination/path.name)
        return manifest
    finally:shutil.rmtree(stage,ignore_errors=True)

class RagEngine:
    def __init__(self,snapshot,encoder,reranker=None,*,strategy='auto'):
        if strategy not in {'auto','raw','bounded'}:raise ValueError('Unknown strategy')
        self.folder=Path(snapshot).absolute();self.encoder=encoder;self.reranker=reranker
        self.index=self.documents=self.source=None
        manifest=_load_snapshot(self.folder)
        if manifest['encoder']!=encoder_identity(encoder):raise ValueError('Snapshot and query encoder fingerprints/policy differ')
        allowed={'documents.jsonl','document_offsets.npy','vectors.npy','vectors.etm'}
        source_name=manifest.get('source_file')
        if source_name not in {'vectors.npy','vectors.etm'}:raise ValueError('Invalid vector source path')
        if set(manifest['files'])!={'documents.jsonl','document_offsets.npy',source_name}:raise ValueError('Invalid snapshot file set')
        for name,meta in manifest['files'].items():
            if name not in allowed:raise ValueError('Invalid snapshot file path')
            if (not isinstance(meta,dict) or set(meta)!={'bytes','sha256'}
                or isinstance(meta['bytes'],bool) or not isinstance(meta['bytes'],int) or meta['bytes']<0
                or not isinstance(meta['sha256'],str) or len(meta['sha256'])!=64
                or any(c not in '0123456789abcdef' for c in meta['sha256'])):
                raise ValueError('Invalid snapshot file metadata')
            path=self.folder/name
            if path.is_symlink() or not path.is_file() or path.stat().st_size!=meta['bytes'] or file_sha256(path)!=meta['sha256']:
                raise ValueError(f'Snapshot file size/hash mismatch: {name}')
        try:
            self.source=(MappedMatrix.open(self.folder/source_name,cache_bytes=0) if source_name.endswith('.etm')
                         else np.load(self.folder/source_name,mmap_mode='r',allow_pickle=False))
            if self.source.shape!=(manifest['rows'],384):raise ValueError('Snapshot vector shape mismatch')
            layout=manifest.get('index_layout','rowwise')
            if layout not in {'rowwise','packed'}:raise ValueError('Unknown snapshot index layout')
            index_class=PackedBoundedIndex if layout=='packed' else BoundedIndex
            self.index=index_class.open(self.folder/'index',self.source)
            self.documents=DocumentStore(self.folder,manifest['rows'])
            # A transparent heuristic for tiny snapshots, not a universal
            # crossover claim. Both paths implement the same exact scorer.
            self.strategy=('raw' if isinstance(self.source,np.ndarray) and manifest['rows']<32768 else 'bounded') if strategy=='auto' else strategy
            if self.strategy=='raw' and not isinstance(self.source,np.ndarray):raise ValueError('Raw strategy requires NPY originals')
            self.manifest=manifest
            self.provenance={'snapshot_sha256':file_sha256(self.folder/'snapshot.json'),
                'encoder':manifest['encoder'],'encoder_runtime':encoder.versions,
                'encoder_threads':encoder.num_threads,
                'reranker_model_sha256':file_sha256(reranker.model_path) if reranker is not None else None,
                'reranker_tokenizer_sha256':reranker.tokenizer_sha256 if reranker is not None else None,
                'reranker_runtime':reranker.versions if reranker is not None else None,
                'reranker_batch_size':32,'reranker_max_length':reranker.max_length if reranker is not None else None}
        except BaseException:self.close();raise

    def search(self,query,*,candidates=20,limit=5,rerank=True):
        if not isinstance(query,str) or not query.strip():raise ValueError('Query must be nonempty text')
        if isinstance(candidates,bool) or not isinstance(candidates,int) or candidates<1:raise ValueError('Invalid candidate count')
        if isinstance(limit,bool) or not isinstance(limit,int) or limit<1:raise ValueError('Invalid result count')
        k=min(candidates,self.manifest['rows']);limit=min(limit,k)
        start=time.perf_counter()
        vector=self.encoder.encode([query],batch_size=1)[0]
        after_encoder=time.perf_counter()
        if self.strategy=='bounded':
            result=self.index.search(vector,k);ids,dense_scores,stats=result.row_ids,result.scores,result.stats
        else:
            all_scores=exact_scores(self.source,vector);ids=stable_topk(all_scores,k);dense_scores=all_scores[ids]
            stats={'candidate_count':len(self.source),'rows':len(self.source),'fallback_reason':None,
                   'certificate':{'valid':True,'excluded_count':0,'reference':'fp32-products-fp64-eight-lanes-no-fma-v1','method':'exhaustive'}}
        after_search=time.perf_counter()
        docs=self.documents.take(ids)
        relevance=None
        if rerank and self.reranker is not None:
            relevance=self.reranker.score(query,[d['title']+' '+d['text'] for d in docs],batch_size=32,sort_by_length=True)
            order=np.lexsort((ids,-relevance))[:limit]
        else:order=np.arange(limit)
        results=[]
        for position in order:
            row={**docs[int(position)],'row_id':int(ids[position]),'dense_score':float(dense_scores[position])}
            if relevance is not None:row['reranker_logit']=float(relevance[position])
            results.append(row)
        done=time.perf_counter()
        return {'query':query,'strategy':self.strategy,'results':results,
            'dense_search':stats,
            'provenance':self.provenance,'rerank_performed':relevance is not None,
            'reranking_scope':f'Only the exact dense top-{k} candidates; not global cross-encoder top-k.' if relevance is not None else 'Reranking not performed.',
            'timing_scope':'Search-call wall time; excludes model/session loading and full snapshot/source verification. No warmup is guaranteed.',
            'timings_ms':{'encoder':1000*(after_encoder-start),'dense_search':1000*(after_search-after_encoder),
                          'documents_and_reranking':1000*(done-after_search),'total':1000*(done-start)}}

    def close(self):
        if self.index is not None:self.index.close();self.index=None
        if self.documents is not None:self.documents.close();self.documents=None
        if isinstance(self.source,MappedMatrix):self.source.close()
        elif self.source is not None and getattr(self.source,'_mmap',None) is not None:self.source._mmap.close()
        self.source=None
    def __enter__(self):return self
    def __exit__(self,*_):self.close()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--threads',type=int,default=1)
    parser.add_argument('--model-file',default=DEFAULT_MODEL)
    parser.add_argument('--encoder-dir',type=Path,default=ROOT/'assets/models/minilm_encoder')
    parser.add_argument('--reranker-dir',type=Path,default=ROOT/'assets/models/minilm_reranker')
    commands=parser.add_subparsers(dest='command',required=True)
    build=commands.add_parser('build');build.add_argument('--corpus',type=Path,required=True)
    build.add_argument('--output',type=Path,required=True);build.add_argument('--compress',action='store_true')
    build.add_argument('--index-layout',choices=['auto','rowwise','packed'],default='auto')
    search=commands.add_parser('search');search.add_argument('--snapshot',type=Path,required=True)
    search.add_argument('--query',required=True);search.add_argument('--candidates',type=int,default=20)
    search.add_argument('--limit',type=int,default=5);search.add_argument('--no-rerank',action='store_true')
    search.add_argument('--strategy',choices=['auto','raw','bounded'],default='auto')
    args=parser.parse_args()
    encoder=MiniLMEncoder(args.encoder_dir,model_path=args.encoder_dir/args.model_file,num_threads=args.threads,max_length=256)
    if args.command=='build':
        output=build_snapshot(args.corpus,args.output,encoder,compress=args.compress,index_layout=args.index_layout)
    else:
        reranker=None if args.no_rerank else MiniLMReranker(args.reranker_dir,model_path=args.reranker_dir/args.model_file,num_threads=args.threads,max_length=256)
        with RagEngine(args.snapshot,encoder,reranker,strategy=args.strategy) as engine:
            output=engine.search(args.query,candidates=args.candidates,limit=args.limit,rerank=not args.no_rerank)
    print(json.dumps(output,indent=2,ensure_ascii=False,allow_nan=False))

if __name__=='__main__':main()
