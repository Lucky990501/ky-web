"""Small Preview admission bound, not a task queue or an authorization bypass."""
GLOBAL_ACTIVE_TASKS=4
ACTIVE_STATUSES=('queued','running','pending','stopping')


class PreviewTaskCapacity(PermissionError):
    pass


def require_task_capacity(store):
    # Called inside the launcher's shared submission lock. Task creation is
    # serialized; execution is not. Existing identity/credit checks still run.
    with store.connection() as connection:
        busy=connection.execute("SELECT count(*) FROM tasks WHERE status IN ('queued','running','pending','stopping')").fetchone()[0]
    if busy>=GLOBAL_ACTIVE_TASKS:
        raise PreviewTaskCapacity('体验服务已有4个任务执行中，请等待一个任务完成后提交；未调用模型。')
