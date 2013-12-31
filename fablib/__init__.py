from fabric.api import execute, with_settings, settings, hide, run
from fablib.logging import log_info


def execute_task_name(task_name, *args):
    log_info('Executing task %s' % task_name)
    t = globals()[task_name]
    return execute(t, *args)


@with_settings(warn_only=True, runner=run)
def rexists(name):
    result = False
    with settings(hide('stderr', 'stdout', 'running', 'warnings')):
        result = runner('ls -l %s' % (name), pty=False)

    return result and result.succeeded
