"""Versioned local training with content-free, persistent live progress."""
import fcntl
import json
import os
import time
import uuid

from . import config
from .policy import OWNER_POLICY

BUSY = ('exporting','preparing','training','validating')


def write_status(**values):
    path=config.HOME/'training-status.json'
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(dict(updated=time.time(),**values)))
    temporary.replace(path)


def status():
    path=config.HOME/'training-status.json'
    result=json.loads(path.read_text()) if path.exists() else {'stage':'idle'}
    if result.get('stage') in BUSY:
        try:
            os.kill(result['pid'],0)
        except (OSError,KeyError):
            result.update(stage='interrupted',error='Training stopped. Start a new run from Setup. Your previous model is preserved.')
    if result.get('started'):
        result['elapsed_seconds']=round(result.get('finished',time.time())-result['started'])
    return result


def main():
    from .training import export,train
    config.private_dir()
    with (config.HOME/'training.lock').open('a+') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            return
        run_id=time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]
        folder=config.HOME/'runs'/run_id
        config.private_dir(folder)
        values=dict(run_id=run_id,pid=os.getpid(),started=time.time(),stage='exporting',
                    progress_percent=0,training_policy=OWNER_POLICY,incoming_messages_included=0,
                    scope='Only your outgoing messages; no incoming training context')
        def progress(**changes):
            values.update(changes)
            write_status(**values)
        try:
            config.update(lambda c:c.update(enabled=False,revision=c.get('revision',0)+1))
            progress()
            cfg=config.load()
            style=cfg.get('training_recipe')=='owner_style_v2'
            if style:
                from .style_training import export_style
                export_style(output_dir=folder/'data',progress=progress)
            else:export(output_dir=folder/'data',progress=progress)
            metadata=train(iters=cfg.get('style_training_steps',600) if style else None,data_dir=folder/'data',adapter_dir=folder/'adapters',progress=progress)
            if style:
                from .quality import evaluate
                progress(stage='validating',quality_check='Checking answers before activating the new adapter')
                report=evaluate(dict(config.load(),adapter_path=str(folder/'adapters')))
                (folder/'quality-report.json').write_text(json.dumps(report,indent=2))
                if report['passed']!=report['total']:
                    raise ValueError('The candidate did not pass the reply checks; the previous adapter is preserved.')
                progress(quality_passed=report['passed'],quality_total=report['total'])
            # Switch only after successful completion. Keep the previous model
            # intact and retain the paused state while a new version is trained.
            config.update(lambda c:c.update(adapter_path=str(folder/'adapters'),enabled=False,revision=c.get('revision',0)+1))
            progress(stage='ready',progress_percent=100,steps_completed=metadata['iterations'],
                     steps_total=metadata['iterations'],eta_seconds=0,finished=time.time())
        except BaseException as exc:
            progress(stage='interrupted' if isinstance(exc,KeyboardInterrupt) else 'failed',finished=time.time(),
                     error='The candidate failed reply checks. Your previous model is preserved.' if (folder/'quality-report.json').exists() else 'Training did not finish. Your previous model is preserved. Details are in .local/training.log.')
            raise


if __name__=='__main__':
    main()
