"""Bounded CLI smoke for an EXE or source command; disposable local inputs only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
CHECKS=('account_response_normalization_checked','activity_response_normalization_checked','exact_response_bundle_checked','provider_results_review_checked','provider_results_changed_result_denied','provider_budget_recovery_checked','account_pacing_recovery_checked','catalog_allowlist_checked','observation_crypto_checked','missing_account_probe_preserved','missing_recovery_preserved','missing_activity_preserved',
        'private_account_review_checked','public_current_account_review_checked','private_execution_quality_checked','private_quote_status_checked','unconfirmed_quote_read_denied','encrypted_quote_evidence_checked','invalid_quote_export_denied','private_files_unchanged','invalid_command_denied',
        'emergency_stop_independent','emergency_stop_storage_contention',
        'emergency_stop_cpu_pressure','order_offload_checked','default_data_not_created')


def _require(condition,code):
    if not condition:raise ValueError('PACKAGED_PAPER_TOOLS_'+code)


def _snapshot(root):
    return sorted((str(p.relative_to(root)),hashlib.sha256(p.read_bytes()).hexdigest())
        for p in root.rglob('*') if p.is_file())


def _call(command,args,env,*,timeout=30):
    if type(timeout) not in (int,float) or not 1<=timeout<=60:
        raise ValueError('PACKAGED_PAPER_TOOLS_TIMEOUT')
    process=subprocess.Popen([*command,*args],cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        text=True,encoding='utf-8',errors='replace')
    try:stdout,stderr=process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Only the smoke's own child tree, including the onefile bootloader.
        if os.name=='nt':subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,timeout=10)
        else:process.kill()
        process.communicate(timeout=10)
        raise ValueError('PACKAGED_PAPER_TOOLS_CHILD_TIMEOUT') from None
    _require(len(stdout.encode())+len(stderr.encode())<4*1024*1024,'OUTPUT_CAP')
    return process.returncode,stdout,stderr


def _json_call(command,args,env,*,timeout=30):
    # Preserve the original three-argument seam for default callers and tests;
    # only the explicitly slower crypto check needs to override the bound.
    result=_call(command,args,env) if timeout==30 else _call(command,args,env,timeout=timeout)
    code,stdout,stderr=result
    _require(code==0 and not stderr,'CHILD_FAILED')
    try:return json.loads(stdout)
    except (ValueError,TypeError):raise ValueError('PACKAGED_PAPER_TOOLS_CHILD_JSON') from None


def _cpu_pressure_workers():
    count=max(1,min(4,os.cpu_count() or 1))
    script=('import time\nend=time.monotonic()+45\nx=1\n'
        'while time.monotonic()<end:x=(x*1103515245+12345)&0x7fffffff\n')
    return [subprocess.Popen([sys.executable,'-c',script],cwd=ROOT,
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL) for _ in range(count)]


def _stop_workers(workers):
    for worker in workers:
        if worker.poll() is None:worker.terminate()
    for worker in workers:
        try:worker.wait(timeout=5)
        except subprocess.TimeoutExpired:
            worker.kill();worker.wait(timeout=5)


def _terminate_child(process):
    if process is None or process.poll() is not None:return
    if os.name=='nt':subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,timeout=10)
    else:process.kill()
    process.communicate(timeout=10)


def _contended_emergency_stop(command,env,root,cpu_pressure=False):
    """Require the external cut latch to win before a held SQLite writer."""
    from nexus import emergency_intent
    from nexus.durable_orders import DurableOrderGateway
    order_path=root/'order_lifecycle.sqlite3';gateway=DurableOrderGateway(order_path)
    gateway.submit('contention-fixture','BTC-USD','BUY',1,tier='terrestrial')
    lock=sqlite3.connect(order_path,timeout=10);process=None
    workers=_cpu_pressure_workers() if cpu_pressure else []
    try:
        if workers:
            time.sleep(.2)
            _require(all(worker.poll() is None for worker in workers),'EMERGENCY_STOP_CPU_PRESSURE')
        lock.execute('BEGIN IMMEDIATE')
        process=subprocess.Popen([*command,'--emergency-stop'],cwd=ROOT,env=env,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace')
        # A frozen one-file Windows launcher can spend several seconds unpacking
        # before it reaches the latch; this bound is still below the smoke's
        # ordinary child timeout and does not release the SQLite writer.
        deadline=time.monotonic()+(20 if cpu_pressure else 15)
        while time.monotonic()<deadline and not emergency_intent.requested(order_path):
            if process.poll() is not None:break
            time.sleep(.01)
        _require(emergency_intent.requested(order_path),'EMERGENCY_STOP_CONTENTION')
        _require(process.poll() is None,'EMERGENCY_STOP_CONTENTION')
        if workers:_require(all(worker.poll() is None for worker in workers),'EMERGENCY_STOP_CPU_PRESSURE')
        started=time.monotonic()
        try:gateway.submit('post-cut-fixture','BTC-USD','BUY',1,tier='terrestrial')
        except ValueError as error:_require(str(error)=='EMERGENCY_CUT_REQUESTED','EMERGENCY_STOP_CONTENTION')
        else:_require(False,'EMERGENCY_STOP_CONTENTION')
        _require(time.monotonic()-started<3,'EMERGENCY_STOP_CONTENTION')
    except BaseException:
        _terminate_child(process);_stop_workers(workers)
        raise
    finally:
        try:lock.rollback()
        finally:lock.close()
    try:
        try:stdout,stderr=process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            _terminate_child(process)
            raise ValueError('PACKAGED_PAPER_TOOLS_CHILD_TIMEOUT') from None
    finally:_stop_workers(workers)
    _require(len(stdout.encode())+len(stderr.encode())<4*1024*1024,'OUTPUT_CAP')
    _require(process.returncode==0 and not stderr,'EMERGENCY_STOP_CONTENTION')
    try:cut=json.loads(stdout)
    except (ValueError,TypeError):raise ValueError('PACKAGED_PAPER_TOOLS_CHILD_JSON') from None
    recovered=DurableOrderGateway(order_path).recovery_report()
    _require(cut['passed'] is True and cut['emergency_cut_state']=='VERIFIED' and
        cut['intent_preceded_durable_halt'] is True and recovered['halted'] is True and
        len(recovered['orders'])==1 and recovered['orders'][0]['status']=='SIMULATED_CANCELLED',
        'EMERGENCY_STOP_CONTENTION')
    if cpu_pressure:
        _require(cut['emergency_priority_attempted'] is (os.name=='nt') and
            cut['emergency_priority_confirmed'] is (os.name=='nt') and
            cut['emergency_priority_class']==('HIGH' if os.name=='nt' else 'STANDARD'),
            'EMERGENCY_STOP_CPU_PRESSURE')


def _windows_read_only_emergency_stop(command,env,root):
    """Require a native read-only journal fault to retain the independent cut.

    Windows ``chmod`` changes the DOS read-only attribute.  The first command
    must therefore publish the separate intent but cannot acknowledge durable
    SQLite cleanup.  After restoring write access, one explicit operator retry
    must finish cancellation without clearing or replacing the original latch.
    """
    _require(os.name=='nt','EMERGENCY_STOP_READ_ONLY_PLATFORM')
    from nexus import emergency_intent
    from nexus.durable_orders import DurableOrderGateway
    order_path=root/'order_lifecycle.sqlite3';gateway=DurableOrderGateway(order_path)
    gateway.submit('read-only-fixture','BTC-USD','BUY',1,tier='terrestrial')
    os.chmod(order_path,stat.S_IREAD)
    try:
        code,stdout,stderr=_call(command,['--emergency-stop'],env)
        _require(code==2 and not stderr,'EMERGENCY_STOP_READ_ONLY_STORAGE')
        try:cut=json.loads(stdout)
        except (ValueError,TypeError):raise ValueError('PACKAGED_PAPER_TOOLS_CHILD_JSON') from None
        _require(cut['passed'] is False and cut['halted'] is True and
            cut['emergency_cut_state']=='UNCONFIRMED' and
            cut['intent_preceded_durable_halt'] is True and
            cut['emergency_intent_present'] is True and
            cut['durable_order_halt_confirmed'] is False and
            emergency_intent.requested(order_path),'EMERGENCY_STOP_READ_ONLY_STORAGE')
        started=time.monotonic()
        try:gateway.submit('post-read-only-cut','BTC-USD','BUY',1,tier='terrestrial')
        except ValueError as error:
            _require(str(error)=='EMERGENCY_CUT_REQUESTED','EMERGENCY_STOP_READ_ONLY_STORAGE')
        else:_require(False,'EMERGENCY_STOP_READ_ONLY_STORAGE')
        _require(time.monotonic()-started<3,'EMERGENCY_STOP_READ_ONLY_STORAGE')
    finally:
        os.chmod(order_path,stat.S_IREAD | stat.S_IWRITE)
    before=gateway.recovery_report()
    _require(before['halted'] is False and len(before['orders'])==1 and
        before['orders'][0]['status']=='SIMULATED_ACK_ONLY','EMERGENCY_STOP_READ_ONLY_STORAGE')
    recovered=_json_call(command,['--emergency-stop'],env)
    after=gateway.recovery_report()
    _require(recovered['passed'] is True and recovered['emergency_cut_state']=='VERIFIED' and
        after['halted'] is True and len(after['orders'])==1 and
        after['orders'][0]['status']=='SIMULATED_CANCELLED' and
        emergency_intent.requested(order_path),'EMERGENCY_STOP_READ_ONLY_STORAGE')


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
            set(catalog['commands'])=={'account-probe','account-recovery','account-response-review','activity','activity-response-review','response-bundle-review','account-review','observation-crypto','order-offload','quality-review','provider-recovery','provider-results','quote-capture','quote-evidence','recovery'} and all(catalog[k] is False for k in
            ('default_data_created','desktop_started','automatic_read','automatic_retry','broker_connected','live_orders_enabled','money_movement_enabled')) and
            catalog['emergency_stop_command']=='--emergency-stop','CATALOG')
        # The fixed encrypted-evidence self-tests are intentionally expensive
        # and can approach 30 seconds on a constrained source or Windows runner.
        # Only these two smoke children get the wider bounded startup/KDF budget.
        encrypted=_json_call(command,['--paper-tool','observation-crypto','self-test'],env,timeout=60)
        _require(encrypted['passed'] is True and encrypted['roundtrip_matches'] is True and
            encrypted['ciphertext_randomized'] is True and encrypted['wrong_password_rejected'] is True and
            all(encrypted[k] is False for k in ('password_saved','vault_accessed','external_connections',
                'original_plaintext_deleted','output_file_writes','ready_for_routing','live_orders_enabled')),
            'OBSERVATION_CRYPTO')
        from nexus.paper_quote_bundle import CHECKS as BUNDLE_CHECKS,FLAGS as BUNDLE_FLAGS
        bundle=_json_call(command,['--paper-tool','quote-evidence','self-test'],env,timeout=60)
        _require(bundle['passed'] is True and all(bundle[k] is True for k in BUNDLE_CHECKS) and
            all(bundle[k] is False for k in (*BUNDLE_FLAGS,'actual_provider_access','actual_vault_access')),'QUOTE_EVIDENCE')
        code,stdout,stderr=_call(command,['--paper-tool','quote-evidence','seal'],env)
        _require(code==2 and stdout.strip()=='PAPER_QUOTE_BUNDLE_FAILED' and not stderr,'INVALID_QUOTE_EXPORT')
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
        from nexus.paper_execution_quality_review import _instant_ns, FLAGS as QUALITY_FLAGS
        epoch=_instant_ns('2026-10-17T18:00:00Z');benchmark=private/'benchmark.json'
        benchmark.write_text(canonical(dict(decision_mid='100',decision_at_ns=epoch-1,request_event_at_ns=epoch,
            arrival_bid='99',arrival_ask='101',arrival_at_ns=epoch,end_at_ns=epoch+3_000_000_000,
            unfilled_mark=None)),encoding='utf-8')
        from nexus.paper_quote_capture import _fixture as quote_fixture,FLAGS as QUOTE_FLAGS
        quote_options,quote_checks=quote_fixture(private/'quote-capture')
        _require(quote_checks['passed'] is True,'QUOTE_FIXTURE')
        from nexus.paper_provider_result_tool import _fixture as provider_fixture
        from nexus.paper_provider_result_review import FLAGS as PROVIDER_RESULT_FLAGS
        provider_input=provider_fixture(private/'provider-results')
        from nexus.paper_account_response_review import _fixture as response_fixture,FLAGS as RESPONSE_FLAGS
        response_options=response_fixture(private/'account-response-review')
        from nexus.paper_activity_response_review import _fixture as activity_response_fixture,FLAGS as ACTIVITY_RESPONSE_FLAGS
        activity_response_options=activity_response_fixture(private/'activity-response-review')
        from nexus.paper_response_bundle_review import _fixture as response_bundle_fixture,FLAGS as RESPONSE_BUNDLE_FLAGS
        response_bundle_options=response_bundle_fixture(private/'response-bundle-review')
        changed_provider_input=provider_input.with_name('changed.json')
        changed_provider=json.loads(provider_input.read_text(encoding='utf-8'))
        changed_provider['accounts'][0]['bindings'][0]['normalized_result']['cash']='999'
        changed_provider_input.write_text(canonical(changed_provider),encoding='utf-8')
        from nexus.paper_provider_read_budget import PaperProviderReadBudget,ZERO
        recovery_budget=PaperProviderReadBudget(private/'provider-recovery.sqlite3')
        with recovery_budget.ownership() as owner:
            token=recovery_budget.begin(account_id=account,account_before_head_sha256=ZERO,
                operation='ACCOUNT_INVENTORY',admission=lambda:1000,ownership=owner)
            recovery_budget.finish(token,account_after_head_sha256='a'*64,actual_gets=4,
                admission=lambda:1001,ownership=owner,status_code=401)
        blocked=recovery_budget.inspect()
        recovered_budget=_json_call(command,['--paper-tool','provider-recovery',
            '--provider-budget',str(recovery_budget.path),'--expected-head',blocked['head_sha256'],
            '--expected-account',account,'--confirm-reviewed-block','--confirm-preserve-uncertainty'],env)
        recovered_state=recovery_budget.inspect()
        _require(recovered_budget['mode']=='PAPER_PROVIDER_READ_BUDGET_REVIEWED_RECOVERY' and
            recovered_budget['review_block_cleared'] is True and recovered_budget['uncertainty_preserved'] is True and
            recovered_budget['cooldown_shortened'] is False and recovered_budget['history_rewritten'] is False and
            recovered_budget['session_capacity_reclaimed'] is False and recovered_budget['next_allowed_at']==blocked['next_allowed_at'] and
            recovered_state['recoveries']==1 and recovered_state['review_required'] is False and
            all(recovered_budget[k] is False for k in ('admission_reserved','automatic_retry','automatic_continuation',
                'broker_wide_quota_verified','distributed_ownership_verified','ready_for_routing',
                'live_orders_enabled','money_movement_enabled','release_approved')),'PROVIDER_BUDGET_RECOVERY')
        from nexus.paper_read_pacing import PaperReadPacing
        recovery_pacing=PaperReadPacing(private/'account-recovery.sqlite3',account)
        recovery_token=recovery_pacing.begin(admission=lambda:1000)
        recovery_pacing.finish(recovery_token,admission=lambda:1001,status_code=401)
        account_blocked=recovery_pacing.inspect()
        recovered_account=_json_call(command,['--paper-tool','account-recovery',
            '--pacing',str(recovery_pacing.path),'--expected-head',account_blocked['head_sha256'],
            '--expected-account',account,'--confirm-reviewed-block','--confirm-preserve-uncertainty'],env)
        account_recovered=recovery_pacing.inspect()
        _require(recovered_account['mode']=='PAPER_READ_PACING_REVIEWED_RECOVERY' and
            recovered_account['review_block_cleared'] is True and recovered_account['uncertainty_preserved'] is True and
            recovered_account['cooldown_shortened'] is False and recovered_account['history_rewritten'] is False and
            recovered_account['session_capacity_reclaimed'] is False and
            recovered_account['next_allowed_at']==account_blocked['next_allowed_at'] and
            account_recovered['recoveries']==1 and account_recovered['review_required'] is False and
            all(recovered_account[k] is False for k in ('admission_reserved','automatic_retry','automatic_continuation',
                'broker_wide_quota_verified','distributed_ownership_verified','ready_for_routing',
                'live_orders_enabled','money_movement_enabled','release_approved')),'ACCOUNT_PACING_RECOVERY')
        before=_snapshot(private)
        response_args=['--paper-tool','account-response-review']
        for flag,key in (('pacing','pacing_path'),('expected-account','expected_account_id'),
                         ('expected-head','expected_head'),('session-head','session_head'),
                         ('account-before','account_before_path'),('positions','positions_path'),
                         ('orders','orders_path'),('account-after','account_after_path'),
                         ('observation','observation_path')):
            response_args+=['--'+flag,str(response_options[key])]
        response_review=_json_call(command,response_args,env)
        _require(response_review['mode']=='PAPER_ACCOUNT_RESPONSE_NORMALIZATION_REVIEW' and
            response_review['exact_response_bytes_match'] is True and response_review['normalization_replayed'] is True and
            len(response_review['account_fields_sha256'])==64 and
            response_review['portable_observation_matches'] is True and response_review['journal_rechecked_unchanged'] is True and
            all(response_review[k] is False for k in RESPONSE_FLAGS),'ACCOUNT_RESPONSE_REVIEW')
        activity_response_args=['--paper-tool','activity-response-review']
        for flag,key in (('pacing','pacing_path'),('activity-store','activity_store_path'),
                         ('expected-account','expected_account_id'),('expected-pacing-head','expected_pacing_head'),
                         ('expected-activity-head','expected_activity_head'),('session-head','session_head'),
                         ('account-before','account_before_path'),('account-after','account_after_path')):
            activity_response_args+=['--'+flag,str(activity_response_options[key])]
        for path in activity_response_options['activity_page_paths']:
            activity_response_args+=['--activity-page',str(path)]
        activity_response_review=_json_call(command,activity_response_args,env)
        _require(activity_response_review['mode']=='PAPER_ACTIVITY_RESPONSE_NORMALIZATION_REVIEW' and
            activity_response_review['exact_response_bytes_match'] is True and
            len(activity_response_review['account_fields_sha256'])==64 and
            activity_response_review['activity_normalization_replayed'] is True and
            activity_response_review['collection_observation_replayed'] is True and
            activity_response_review['retained_receipt_replayed'] is True and
            activity_response_review['journals_rechecked_unchanged'] is True and
            all(activity_response_review[k] is False for k in ACTIVITY_RESPONSE_FLAGS),'ACTIVITY_RESPONSE_REVIEW')
        response_bundle_args=['--paper-tool','response-bundle-review']
        for flag,key in (('pacing','pacing_path'),('activity-store','activity_store_path'),
                ('expected-account','expected_account_id'),('expected-pacing-head','expected_pacing_head'),
                ('expected-activity-head','expected_activity_head'),
                ('account-session-head','account_session_head'),('account-before','account_before_path'),
                ('positions','positions_path'),('orders','orders_path'),('account-after','account_after_path'),
                ('account-observation','account_observation_path'),
                ('activity-session-head','activity_session_head'),
                ('activity-account-before','activity_account_before_path'),
                ('activity-account-after','activity_account_after_path')):
            response_bundle_args+=['--'+flag,str(response_bundle_options[key])]
        for path in response_bundle_options['activity_page_paths']:
            response_bundle_args+=['--activity-page',str(path)]
        response_bundle=_json_call(command,response_bundle_args,env)
        _require(response_bundle['mode']=='PAPER_EXACT_RESPONSE_BUNDLE_REVIEW' and
            response_bundle['exact_response_bytes_match'] is True and
            response_bundle['account_normalization_replayed'] is True and
            response_bundle['activity_normalization_replayed'] is True and
            response_bundle['sessions_adjacent'] is True and
            response_bundle['activity_session_is_current_tail'] is True and
            response_bundle['adjacent_account_fields_match'] is True and
            len(response_bundle['adjacent_account_fields_sha256'])==64 and
            response_bundle['journals_rechecked_unchanged'] is True and
            all(response_bundle[k] is False for k in RESPONSE_BUNDLE_FLAGS),'EXACT_RESPONSE_BUNDLE')
        provider_result=_json_call(command,['--paper-tool','provider-results','--input',str(provider_input)],env)
        _require(provider_result['local_provider_results_consistent'] is True and provider_result['normalized_sessions_matched']==5 and
            provider_result['status']=='REVIEW_REQUIRED' and all(provider_result[k] is False for k in PROVIDER_RESULT_FLAGS),'PROVIDER_RESULTS')
        changed_result=_json_call(command,['--paper-tool','provider-results','--input',str(changed_provider_input)],env)
        _require(changed_result['local_provider_results_consistent'] is False and
            'ACCOUNT_NORMALIZED_EVIDENCE_UNVERIFIED' in changed_result['review_reasons'] and
            all(changed_result[k] is False for k in PROVIDER_RESULT_FLAGS),'PROVIDER_RESULTS')
        args=['--paper-tool','account-review']
        for name,key in (('expected-account','expected_account_id'),('request-journal','request_journal_path'),
                         ('pacing','pacing_path'),('activity-store','activity_store_path'),
                         ('acceptance-store','acceptance_store_path'),('request-head','expected_request_head'),('activity-head','expected_activity_head')):
            args+=['--'+name,str(options[key])]
        observed=_json_call(command,[*args,'--opening',str(opening),'--closing',str(closing)],env)
        _require(observed['mode']=='PAPER_ACCOUNT_EVIDENCE_BUNDLE_REVIEW' and observed['local_request_terms_agree'] is True and
            observed['explicit_fills_reviewed']==2 and observed['gross_only_minus_observed']=={'USD':'0','BTC':'0'} and
            all(observed[k] is False for k in FLAGS),'ACCOUNT_REVIEW')
        current_args=list(args)
        for flag in ('--request-head','--activity-head'):
            index=current_args.index(flag);del current_args[index:index+2]
        public=_json_call(command,[*current_args,'--opening',str(opening),'--closing',str(closing),
            '--use-current-heads','--public-summary'],env)
        from nexus.paper_account_evidence_review import public_summary
        _require(public==public_summary(observed),'PUBLIC_ACCOUNT_REVIEW')
        encoded=json.dumps(public)
        _require(all(value not in encoded for value in (account,str(root),options['expected_request_head'],
            options['expected_activity_head'],'nx_accept_fixture','802','1000')),'PUBLIC_ACCOUNT_REVIEW')
        quality_args=list(args);quality_args[1]='quality-review'
        reviewed=_json_call(command,[*quality_args,'--client-order-id','nx_accept_fixture','--benchmark',str(benchmark)],env)
        q=reviewed['execution_quality']
        _require(reviewed['mode']=='PAPER_EXECUTION_QUALITY_REVIEW' and reviewed['all_retained_original_fills_included'] is True and
            reviewed['local_request_event_bound'] is True and reviewed['request_event_at_ns']==epoch and
            reviewed['benchmark_request_event_time_verified'] is False and reviewed['journals_rechecked_unchanged'] is True and reviewed['request_head_sha256']==options['expected_request_head'] and
            reviewed['activity_head_sha256']==options['expected_activity_head'] and q['unique_fills']==2 and
            q['fill_vwap']=='99' and q['signed_gross_shortfall_usd']=='-2' and q['signed_shortfall_with_supplied_fees_usd'] is None and
            all(reviewed[k] is False for k in QUALITY_FLAGS),'QUALITY_REVIEW')
        quote_args=['--paper-tool','quote-capture']
        for flag,key in (('expected-account','expected_account_id'),('request-journal','request_journal_path'),
                         ('quote-store','quote_store_path'),('pacing','pacing_path'),('request-head','expected_request_head')):
            quote_args+=['--'+flag,str(quote_options[key])]
        quoted=_json_call(command,[*quote_args,'--status-only'],env)
        _require(quoted['mode']=='PAPER_QUOTE_CAPTURE_STATUS' and quoted['local_capture_history_agreement'] is True and
            quoted['retained_quotes']==1 and quoted['retained_pacing_sessions']==1 and all(quoted[k] is False for k in QUOTE_FLAGS),'QUOTE_STATUS')
        code,stdout,stderr=_call(command,quote_args,env)
        _require(code==2 and stdout.strip()=='PAPER_QUOTE_CAPTURE_FAILED' and not stderr,'UNCONFIRMED_QUOTE_READ')
        _require(_snapshot(private)==before,'PRIVATE_FILES_CHANGED')
        from nexus.durable_orders import DurableOrderGateway
        from nexus.tier_permissions import configure as configure_tier
        live=root/'offload-live';live.mkdir();source=live/'order_lifecycle.sqlite3';destination=root/'offload-copy'
        replacement=root/'offload-replacement';install_recovery=root/'offload-install-recovery'
        configure_tier('terrestrial',True)
        gateway=DurableOrderGateway(source);gateway.initialize_account(10000);gateway.link_portfolio()
        for side in ('BUY','SELL'):
            order_id='offload-'+side.lower();gateway.submit(order_id,'BTC-USD',side,50,tier='terrestrial')
            gateway.set_execution_terms(order_id,1000,10)
            gateway.simulate_quote_fill(order_id,'fill',1,1,25,25,2,1001,1001)
            gateway.cancel(order_id)
        gateway.emergency_halt();gateway.archive_terminal('packaged_fixture')
        source_before=(source.read_bytes(),(live/'paper_portfolio.sqlite3').read_bytes())
        copied=_json_call(command,['--paper-tool','order-offload','create',str(source),str(destination),'--confirm-application-closed'],env)
        verified=_json_call(command,['--paper-tool','order-offload','verify',str(destination)],env)
        planned=_json_call(command,['--paper-tool','order-offload','rotation-plan',str(source),str(destination),'--confirm-application-closed'],env)
        prepared=_json_call(command,['--paper-tool','order-offload','prepare-replacement',str(source),str(destination),
            str(replacement),'--confirm-application-closed'],env)
        replacement_verified=_json_call(command,['--paper-tool','order-offload','verify-replacement',str(destination),
            str(replacement)],env)
        installed=_json_call(command,['--paper-tool','order-offload','install-replacement',str(source),str(destination),
            str(replacement),str(install_recovery),'--confirm-application-closed'],env)
        install_recovery_verified=_json_call(command,['--paper-tool','order-offload','verify-install-recovery',
            str(destination),str(install_recovery)],env)
        rolled_back=_json_call(command,['--paper-tool','order-offload','rollback-install',str(source),str(destination),
            str(install_recovery),'--confirm-application-closed'],env)
        _require(copied['verified'] is True and copied['source_unchanged'] is True and verified['verified'] is True and
            copied['capacity_reclaimed'] is False and
            copied['source_history_deleted'] is False and copied['live_orders_enabled'] is False and
            planned['exact_source_bound'] is True and planned['dry_run_only'] is True and
            planned['source_unchanged'] is True and planned['archive_unchanged'] is True and
            planned['compaction_performed'] is False and planned['capacity_reclaimed'] is False and
            planned['source_history_deleted'] is False and prepared['replacement_candidate_only'] is True and
            prepared['verified'] is True and prepared['source_preserved'] is True and
            prepared['archive_preserved'] is True and prepared['automatic_install'] is False and
            prepared['capacity_reclaimed'] is False and prepared['source_history_deleted'] is False and
            replacement_verified['verified'] is True and replacement_verified['halted'] is True and
            replacement_verified['flat'] is True and replacement_verified['live_orders_enabled'] is False and
            installed['installed'] is True and installed['capacity_reclaimed'] is True and
            installed['automatic_resume'] is False and installed['rollback_available'] is True and
            install_recovery_verified['verified'] is True and install_recovery_verified['source_preserved'] is True and
            rolled_back['restored'] is True and rolled_back['capacity_reclaimed'] is False and
            (source.read_bytes(),(live/'paper_portfolio.sqlite3').read_bytes())==source_before,
            'ORDER_OFFLOAD')
        code,stdout,stderr=_call(command,['--paper-tool','__not_allowed__','PRIVATE_ARGUMENT_FIXTURE'],env)
        _require(code==2 and stdout=='' and stderr.strip()=='NEXUS_PAPER_TOOL_USAGE_ERROR','INVALID_COMMAND')
        cut_root=root/'explicit-emergency-cut';cut_env=dict(env,NEXUS_PORTABLE_DATA=str(cut_root))
        cut=_json_call(command,['--emergency-stop'],cut_env)
        _require(cut['mode']=='NEXUS_LOCAL_PAPER_EMERGENCY_STOP' and cut['passed'] is True and
            cut['emergency_cut_state']=='VERIFIED' and cut['intent_preceded_durable_halt'] is True and
            cut['emergency_intent_present'] is True and cut['durable_order_halt_confirmed'] is True and
            all(cut[k] is False for k in ('desktop_started','network_requested','credential_access_requested',
                'broker_connected','live_orders_enabled','money_movement_enabled','reset_available','automatic_restart')) and
            (cut_root/'emergency_cut.intent').is_file() and (cut_root/'order_lifecycle.sqlite3').is_file(),
            'EMERGENCY_STOP')
        contention_root=root/'contended-emergency-cut'
        _contended_emergency_stop(command,dict(env,NEXUS_PORTABLE_DATA=str(contention_root)),contention_root)
        pressure_root=root/'cpu-pressure-emergency-cut'
        _contended_emergency_stop(command,dict(env,NEXUS_PORTABLE_DATA=str(pressure_root)),pressure_root,True)
        if os.name=='nt':
            read_only_root=root/'read-only-emergency-cut'
            _windows_read_only_emergency_stop(command,
                dict(env,NEXUS_PORTABLE_DATA=str(read_only_root)),read_only_root)
        _require(not default.exists() and not missing.exists(),'DEFAULT_DATA_CREATED')
        return dict(mode='PACKAGED_PAPER_OPERATOR_TOOLS_SMOKE',passed=True,**{k:True for k in CHECKS},
            tool_invocations=31+int(os.name=='nt'),provider_reads_requested=False,credential_reads_requested=False,
            live_orders_enabled=False,money_movement_enabled=False,release_approved=False,
            host_os='WINDOWS_NT' if os.name=='nt' else 'NON_WINDOWS',
            native_windows_storage_fault_checked=os.name=='nt',
            native_windows_acceptance=False,
            scope='Thirty-one cross-platform command-prefix child processes, plus one native Windows read-only-journal emergency fault when applicable. The explicit account-response-review command replays four private response bodies and one portable observation against an exact account session. The activity-response-review command replays exact private account/page bodies, activity normalization, collection observation and retained receipt against one exact activity session. Response-bundle-review requires those two sessions to be adjacent at the exact current pacing/activity heads and replays every private body again. All three run without credentials, network access or writes. The explicit account-recovery and provider-recovery commands append reviewed exact-head boundaries to blocked disposable local journals while preserving cooldown, uncertainty, history and capacity. The explicit offline provider-results command checks a real five-reader injected manifest and refuses altered normalized evidence without writing files. The offline order-offload flow copies a stopped, flat, reconciled and logically archived disposable journal, binds a halted replacement to it, installs that candidate behind a persistent recovery marker, verifies the retained rollback copy and explicitly rolls the fixture back. It never resumes, deletes the preserved source copy or grants broker, funds, live or release authority. Explicit cuts run with and without a held SQLite writer and under bounded synthetic CPU pressure, with a real in-package authenticated-encryption self-test, disposable local journals, two supplied inventory manifests and a supplied quote benchmark. Pinned private account review and explicitly sampled-current-head public diagnostics must agree without leaking private fields. The quality review derives all retained original-request fills and keeps fees/benchmark authority unknown. Quote capture replays a bound private fixture in status-only mode and refuses an unconfirmed read. The account probe uses its vault/network-silent pacing-status mode; no provider/key read is requested. Contended emergency commands must publish the latch before the writer clears, reject new intake without waiting on SQLite and durably cancel after release. A native read-only fault must remain unconfirmed but fenced until one explicit post-recovery retry completes cleanup. Windows requests high, never realtime, process priority; failure never suppresses the cut attempt. Inventory and full-chain quote-evidence self-tests use distinct authenticated encryption formats and retain no ciphertext, password or plaintext bundle. This is bounded same-host evidence, not whole-machine starvation, an OS-level network/vault audit, actual account acceptance, ordinary-user profile/ACL acceptance or proof of profitability. Private child JSON and paths are omitted.')


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
        allowed={'ACCOUNT_RESPONSE_REVIEW','ACTIVITY_RESPONSE_REVIEW','EXACT_RESPONSE_BUNDLE','PROVIDER_RESULTS','PROVIDER_BUDGET_RECOVERY','ACCOUNT_PACING_RECOVERY','COMMAND','CATALOG','OBSERVATION_CRYPTO','MISSING_ACCOUNT_PROBE','MISSING_RECOVERY','MISSING_ACTIVITY','ACCOUNT_REVIEW','PUBLIC_ACCOUNT_REVIEW','QUALITY_REVIEW',
            'PRIVATE_FILES_CHANGED','ORDER_OFFLOAD','INVALID_COMMAND','EMERGENCY_STOP','EMERGENCY_STOP_CONTENTION','EMERGENCY_STOP_CPU_PRESSURE','DEFAULT_DATA_CREATED','OUTPUT_CAP',
            'EMERGENCY_STOP_READ_ONLY_PLATFORM','EMERGENCY_STOP_READ_ONLY_STORAGE',
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
