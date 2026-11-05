"""Close waiting cards before starting an older runtime that cannot resume them."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.topic_task_store import get_topic_task_store


def main():
    store=get_topic_task_store()
    if store.list_running_tasks(limit=1): raise RuntimeError('Stop ingress and drain running tasks first')
    connection=store._connect(database=store._schema_name())
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version_num FROM alembic_version")
            revisions={row["version_num"] for row in cursor.fetchall()}
        if revisions != {"20261103_000032"}:
            raise RuntimeError(f"Rollback requires revision 20261103_000032, found {revisions}")
    finally:
        connection.close()
    tasks=store.list_parked_tasks(limit=100000)
    for task in tasks:
        error={'code':'runtime_rolled_back','message':'系统已回退，请重新发送请求；当前等待卡片无法继续。'}
        store.update_assistant_message(topic_id=task['topic_id'],task_id=task['task_id'],status='suspended',content='',usage=None,error=error)
        store.finish_task(task_id=task['task_id'],task_status='suspended',error=error)
    from alembic import command
    from alembic.config import Config
    config=Config(str(Path(__file__).resolve().parents[1]/"alembic.ini"))
    config.set_main_option("script_location",str(Path(__file__).resolve().parents[1]/"alembic"))
    # The old entrypoint runs Alembic and must see a revision it understands.
    command.stamp(config,"20261027_000031")
    print(f'Suspended {len(tasks)} waiting tasks; session and result files preserved')


if __name__=='__main__':main()
