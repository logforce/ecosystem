import hashlib
import json
from pathlib import Path
import numpy as np
import pytest

from commercial_lab.rag import build_snapshot, RagEngine, DocumentStore

class FixtureEncoder:
    """Deterministic small model substitute for storage/provenance tests only."""
    max_length=256
    num_threads=1
    versions={'fixture':'1'}
    def __init__(self,path):
        self.model_dir=path
        path.mkdir(exist_ok=True)
        self.model_path=path/'fixture.bin'
        self.model_path.write_bytes(b'fixed fixture weights')
        (path/'config.json').write_text('{"pad_token_id":0}')
        self.tokenizer_sha256=hashlib.sha256(b'fixture tokenizer').hexdigest()
    def encode(self,texts,**kwargs):
        values=np.zeros((len(texts),384),dtype=np.float32)
        for row,text in enumerate(texts):
            for token in text.lower().split():
                values[row,int(hashlib.sha256(token.encode()).hexdigest()[:8],16)%384]+=1
            norm=np.linalg.norm(values[row])
            if norm:values[row]/=norm
        return values

@pytest.fixture
def corpus(tmp_path):
    path=tmp_path/'input.jsonl'
    records=[{'id':'a','title':'Memory','text':'vector memory exact search'},
             {'id':'b','title':'Cooking','text':'bread flour water oven'},
             {'id':'c','title':'Index','text':'vector search index memory'},
             {'id':'d','title':'Music','text':'piano melody harmony'}]
    path.write_text(''.join(json.dumps(r)+'\n' for r in records))
    return path

@pytest.mark.parametrize('index_layout',['rowwise','packed'])
def test_snapshot_raw_bounded_and_compressed_preserve_results(tmp_path,corpus,index_layout):
    encoder=FixtureEncoder(tmp_path/'model')
    raw=tmp_path/'raw';compressed=tmp_path/'compressed'
    build_snapshot(corpus,raw,encoder,index_layout=index_layout)
    build_snapshot(corpus,compressed,encoder,compress=True,index_layout=index_layout)
    outputs=[]
    for path,strategy in [(raw,'raw'),(raw,'bounded'),(compressed,'bounded')]:
        with RagEngine(path,encoder,strategy=strategy) as engine:
            result=engine.search('vector memory search',candidates=3,limit=3,rerank=False)
            outputs.append([(r['id'],r['dense_score']) for r in result['results']])
            assert result['dense_search']['certificate']['valid']
            assert result['rerank_performed'] is False
            assert result['provenance']['encoder']['query_batch_size']==1
    assert outputs[0]==outputs[1]==outputs[2]
    with pytest.raises(FileExistsError):build_snapshot(corpus,raw,encoder)

def test_snapshot_encoder_and_document_tampering_fail_closed(tmp_path,corpus):
    encoder=FixtureEncoder(tmp_path/'model');target=tmp_path/'snapshot'
    build_snapshot(corpus,target,encoder)
    encoder.max_length=128
    with pytest.raises(ValueError,match='fingerprints'):RagEngine(target,encoder)
    encoder.max_length=256
    with (target/'documents.jsonl').open('ab') as f:f.write(b'changed')
    with pytest.raises(ValueError,match='hash mismatch'):RagEngine(target,encoder)

def test_duplicate_manifest_and_symlink_are_rejected(tmp_path,corpus):
    encoder=FixtureEncoder(tmp_path/'model');target=tmp_path/'snapshot'
    build_snapshot(corpus,target,encoder)
    manifest=target/'snapshot.json';original=manifest.read_text()
    manifest.write_text(original.replace('{','{"rows":4,',1))
    with pytest.raises(ValueError,match='Duplicate'):RagEngine(target,encoder)
    manifest.write_text(original)
    link=tmp_path/'linked';link.symlink_to(target,target_is_directory=True)
    with pytest.raises(ValueError,match='symlinked'):RagEngine(link,encoder)

def test_document_offsets_are_checked_and_closing_is_idempotent(tmp_path,corpus):
    encoder=FixtureEncoder(tmp_path/'model');target=tmp_path/'snapshot'
    build_snapshot(corpus,target,encoder)
    store=DocumentStore(target,4)
    assert [r['id'] for r in store.take([2,0,2])]==['c','a','c']
    store.close();store.close()
    np.save(target/'document_offsets.npy',np.array([0,1,1,3,4],dtype='<u8'))
    with pytest.raises(ValueError,match='offsets'):DocumentStore(target,4)
