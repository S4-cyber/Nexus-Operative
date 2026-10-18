"""Bounded CLI smoke for an EXE or source command; disposable local inputs only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
CHECKS=('catalog_allowlist_checked','missing_account_probe_preserved','missing_recovery_preserved','missing_activity_preserved',
        'private_account_review_checked','private_files_unchanged','invalid_command_denied',
        'default_data_not_created')


def _require(condition,code):
    if not condition:raise ValueError('PACKAGED_PAPER_TOOLS_'+code)


def _snapshot(root):
    return sorted((str(p.relative_to(root)),hashlib.sha256(p.read_bytes()).hexdigest())
        for p in root.rglob('*') if p.is_file())


def _call(command,args,env):
    process=subprocess.Popen([*command,*args],cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        text=True,encoding='utf-8',errors='replace')
    try:stdout,stderr=process.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        # Only the smoke's own child tree, including the onefile bootloader.
        if os.name=='nt':subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,timeout=10)
        else:process.kill()
        process.communicate(timeout=10)
        raise ValueError('PACKAGED_PAPER_TOOLS_CHILD_TIMEOUT') from None
    _require(len(stdout.encode())+len(stderr.encode())<4*1024*1024,'OUTPUT_CAP')
    return process.returncode,stdout,stderr


def _json_call(command,args,env):
    code,stdout,stderr=_call(command,args,env)
    _require(code==0 and not stderr,'CHILD_FAILED')
    try:return json.loads(stdout)
    except (ValueError,TypeError):raise ValueError('PACKAGED_PAPER_TOOLS_CHILD_JSON') from None


def run(command):
    from nexus.paper_account_evidence_review import _fixture,FLAGS
    from nexus.paper_acceptance_probe import FLAGS as PROBE_FLAGS
    from nexus.release_roadmap import REVISION
    from nexus.alpaca_paper_contract import canonical
    _require(type(command) is list and 1<=len(command)<=2 and all(type(p) is str for p in command),'COMMAND')
    with tempfile.TemporaryDirectory(prefix='nexus-packaged-paper-tools-') as directory:
        root=Path(directory);missing=root/'missing';default=root/'unexpected-default-data'
        env=dict(os.environ,NEXUS_PORTABLE_DATA=str(default),NEXUS_HEADLESS_MODE='1',NEXUS_BROWSER_MODE='1')
        catalog=_json_call(command,['--paper-tool','list'],env)
        _require(catalog['mode']=='NEXUS_PACKAGED_PAPER_TOOLS' and catalog['version']==REVISION and
            set(catalog['commands'])=={'account-probe','activity','account-review','recovery'} and all(catalog[k] is False for k in
            ('default_data_created','desktop_started','automatic_read','automatic_retry','broker_connected','live_orders_enabled','money_movement_enabled')),'CATALOG')
        account='01234567-89ab-cdef-0123-456789abcdef'
        account_status=_json_call(command,['--paper-tool','account-probe','--expected-account',account,
            '--pacing',str(missing/'pacing.sqlite3'),'--pacing-status-only'],env)
        _require(account_status['mode']=='PAPER_ACCEPTANCE_PACING_STATUS' and account_status['sessions']==0 and
            account_status['journal_present'] is False and all(account_status[k] is False for k in PROBE_FLAGS) and
            not missing.exists(),'MISSING_ACCOUNT_PROBE')
        recovered=_json_call(command,['--paper-tool','recovery','--expected-account',account,
            '--journal',str(missing/'requests.sqlite3'),'--pacing',str(missing/'pacing.sqlite3'),'--review-only'],env)
        _require(recovered['mode']=='PAPER_RECOVERY_STEP_REVIEW' and recovered['selection']['selected_client_order_id'] is None and
            recovered['pacing']['journal_present'] is False and not missing.exists(),'MISSING_RECOVERY')
        activity=_json_call(command,['--paper-tool','activity','--expected-account',account,'--pacing',str(missing/'pacing.sqlite3'),
            '--activity-store',str(missing/'activity.sqlite3'),'--acceptance-store',str(missing/'acceptance.sqlite3'),'--status-only'],env)
        _require(activity['mode']=='PAPER_ACTIVITY_ACCEPTANCE_STATUS' and activity['sessions']==0 and
            activity['accepted_transitions']==0 and not missing.exists(),'MISSING_ACTIVITY')
        private=root/'private';private.mkdir();options=_fixture(private)
        opening=private/'opening.json';closing=private/'closing.json'
        opening.write_text(canonical(options['opening_observation']),encoding='utf-8');closing.write_text(canonical(options['closing_observation']),encoding='utf-8')
        before=_snapshot(private)
        args=['--paper-tool','account-review']
        for name,key in (('expected-account','expected_account_id'),('request-journal','request_journal_path'),
                         ('pacing','pacing_path'),('activity-store','activity_store_path'),
                         ('acceptance-store','acceptance_store_path'),('request-head','expected_request_head'),('activity-head','expected_activity_head')):
            args+=['--'+name,str(options[key])]
        observed=_json_call(command,[*args,'--opening',str(opening),'--closing',str(closing)],env)
        _require(observed['mode']=='PAPER_ACCOUNT_EVIDENCE_BUNDLE_REVIEW' and observed['local_request_terms_agree'] is True and
            observed['explicit_fills_reviewed']==2 and observed['gross_only_minus_observed']=={'USD':'0','BTC':'0'} and
            all(observed[k] is False for k in FLAGS),'ACCOUNT_REVIEW')
        _require(_snapshot(private)==before,'PRIVATE_FILES_CHANGED')
        code,stdout,stderr=_call(command,['--paper-tool','__not_allowed__','PRIVATE_ARGUMENT_FIXTURE'],env)
        _require(code==2 and stdout=='' and stderr.strip()=='NEXUS_PAPER_TOOL_USAGE_ERROR','INVALID_COMMAND')
        _require(not default.exists() and not missing.exists(),'DEFAULT_DATA_CREATED')
        return dict(mode='PACKAGED_PAPER_OPERATOR_TOOLS_SMOKE',passed=True,**{k:True for k in CHECKS},
            tool_invocations=6,provider_reads_requested=False,credential_reads_requested=False,
            live_orders_enabled=False,money_movement_enabled=False,release_approved=False,
            native_windows_acceptance=False,
            scope='Six actual command-prefix child processes with disposable local journals and two supplied inventory manifests. The account probe uses its vault/network-silent pacing-status mode; no provider/key read is requested. This is not an OS-level network/vault audit, actual account acceptance, native-window/profile/ACL acceptance or proof of profitability. Private child JSON and paths are omitted.')


class _Parser(argparse.ArgumentParser):
    def error(self,message):self.exit(2,'PACKAGED_PAPER_TOOLS_USAGE_ERROR\n')


def main():
    parser=_Parser(description=__doc__);parser.add_argument('--executable',type=Path,required=True);parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    try:
        _require(args.executable.is_file(),'EXECUTABLE_REQUIRED')
        report=run([str(args.executable.resolve())]);encoded=json.dumps(report,sort_keys=True,indent=2)+'\n'
        if args.output:args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(encoded,encoding='utf-8')
    except (Exception,KeyboardInterrupt) as error:
        allowed={'COMMAND','CATALOG','MISSING_ACCOUNT_PROBE','MISSING_RECOVERY','MISSING_ACTIVITY','ACCOUNT_REVIEW',
            'PRIVATE_FILES_CHANGED','INVALID_COMMAND','DEFAULT_DATA_CREATED','OUTPUT_CAP',
            'CHILD_FAILED','CHILD_JSON','CHILD_TIMEOUT','EXECUTABLE_REQUIRED'}
        code=str(error) if type(error) is ValueError and str(error) in {'PACKAGED_PAPER_TOOLS_'+k for k in allowed} else 'PACKAGED_PAPER_TOOLS_INTERNAL_ERROR'
        if args.output:
            try:
                args.output.parent.mkdir(parents=True,exist_ok=True)
                args.output.write_text(json.dumps(dict(mode='PACKAGED_PAPER_OPERATOR_TOOLS_SMOKE',passed=False,failure_code=code),sort_keys=True)+'\n',encoding='utf-8')
            except OSError:pass
        print('PACKAGED_PAPER_TOOLS_FAILED');return 2
    print(encoded,end='');return 0


if __name__=='__main__':raise SystemExit(main())
