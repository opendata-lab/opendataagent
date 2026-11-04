import importlib.util
import json
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('migrate_results',Path(__file__).parents[1]/'scripts/migrate_runtime_results.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_legacy_jsonl_converts_to_standard_json_and_preserves_originals(tmp_path):
    old=tmp_path/'topic_test/workspace/.dataagent/results';old.mkdir(parents=True)
    legacy='{"_format":"dataagent_jsonl_v1","total_rows":2}\n{"id":1}\n{"id":2}\n'
    (old/'res_rows.json').write_text(legacy)
    (old/'res_text.json').write_text('plain text')
    assert module.migrate_results(tmp_path)['migrated']==2
    result=json.loads((tmp_path/'topic_test/results/res_rows.json').read_text())
    assert result['rows']==[{'id':1},{'id':2}]
    assert result['_migration']['metadata_missing'] is True
    assert (old/'res_rows.json').read_text()==legacy
    assert module.migrate_results(tmp_path)['existing']==2
    assert (tmp_path/'topic_test/results/res_text.json').read_text()=='plain text'


def test_invalid_migration_removes_partial_output_and_never_overwrites(tmp_path):
    old=tmp_path/'topic_test/workspace/.dataagent/results';old.mkdir(parents=True)
    (old/'bad.json').write_text('{"_format":"dataagent_jsonl_v1"}\nnot-json')
    with pytest.raises(ValueError):module.migrate_results(tmp_path)
    target=tmp_path/'topic_test/results/bad.json'
    assert not target.exists()
    target.write_text('keep')
    assert module.migrate_results(tmp_path)['existing']==1
    assert target.read_text()=='keep'
