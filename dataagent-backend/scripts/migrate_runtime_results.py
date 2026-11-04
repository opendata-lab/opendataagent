"""One-time, idempotent migration from workspace/.dataagent/results to results/."""
from __future__ import annotations
import json
from pathlib import Path
import shutil
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def migrate_results(root:Path)->dict[str,int]:
    counts={'migrated':0,'existing':0,'topics':0}
    for topic in sorted(root.glob('topic_*')):
        if topic.is_symlink(): raise ValueError(f'Symlink topic root refused: {topic}')
        old=topic/'workspace/.dataagent/results'
        if not old.exists(): continue
        if old.is_symlink() or not old.resolve().is_relative_to(topic.resolve()):
            raise ValueError(f'Unsafe legacy results directory: {old}')
        destination=topic/'results'
        if destination.is_symlink(): raise ValueError(f'Symlink results directory refused: {destination}')
        destination.mkdir(parents=True,exist_ok=True)
        counts['topics']+=1
        for source in sorted(old.iterdir()):
            if source.is_symlink(): raise ValueError(f'Symlink legacy result refused: {source}')
            if not source.is_file(): continue
            target=destination/source.name
            if target.exists(): counts['existing']+=1; continue
            created=False
            try:
                with source.open('rb') as incoming,target.open('xb') as outgoing:
                    created=True
                    first=incoming.readline()
                    try: header=json.loads(first)
                    except (ValueError,UnicodeDecodeError): header=None
                    if isinstance(header,dict) and header.get('_format')=='dataagent_jsonl_v1':
                        outgoing.write(b'{"rows":[')
                        count=0
                        for line in incoming:
                            if not line.strip(): continue
                            row=json.loads(line)
                            if count: outgoing.write(b',')
                            outgoing.write(json.dumps(row,ensure_ascii=False).encode())
                            count+=1
                        migration={'source_format':'dataagent_jsonl_v1','rows':count,
                            'declared_rows':header.get('total_rows'),'metadata_missing':True,
                            'notice':'Legacy row-only cache omitted original surrounding metadata; it cannot be reconstructed.'}
                        outgoing.write(b'],"_migration":'+json.dumps(migration,ensure_ascii=False).encode()+b'}')
                    else:
                        outgoing.write(first);shutil.copyfileobj(incoming,outgoing)
                counts['migrated']+=1
            except BaseException:
                if created: target.unlink(missing_ok=True)
                raise
    return counts


def main():
    from config import get_settings
    from core.topic_task_store import get_topic_task_store
    store=get_topic_task_store();cfg=get_settings()
    if store.list_running_tasks(limit=1): raise RuntimeError('Stop ingress and drain running tasks before migration')
    result=migrate_results(Path(cfg.dataagent_runtime_root or cfg.dataagent_host_root).resolve())
    suspended=0
    for task in store.list_parked_tasks(limit=100000):
        checkpoint=store.get_session_checkpoint(task['topic_id'])
        if checkpoint and checkpoint['task_id']==task['task_id']: continue
        error={'code':'runtime_upgraded','message':'系统已升级，请重新发送请求；旧版内存等待无法恢复。'}
        store.update_assistant_message(topic_id=task['topic_id'],task_id=task['task_id'],status='suspended',content='',usage=None,error=error)
        store.finish_task(task_id=task['task_id'],task_status='suspended',error=error)
        suspended+=1
    print(json.dumps({**result,'legacy_waiting_tasks_suspended':suspended}))


if __name__=='__main__':main()
