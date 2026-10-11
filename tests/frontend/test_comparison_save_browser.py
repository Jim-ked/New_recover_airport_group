"""Real page/modules against controlled temporary-source API responses."""
from urllib.parse import urlsplit, parse_qs

import pytest

from tests.frontend.test_results_initialization_browser import browser, server_url, api_payload, initialized, playwright
from tests.services.test_run_comparison_record_service import env, OWNER


@pytest.fixture
def ui(browser,server_url,env):
    _,service,runs=env
    context=browser.new_context(viewport={'width':1440,'height':1000})
    context.add_cookies([{'name':'csrftoken','value':'browser-test-csrf','url':server_url}])
    page=context.new_page()
    state={'page':page,'service':service,'held_saves':[],'held_comparisons':[], 'delay_comparison':False,
           'strip_provenance':False,'ineligible':False,'viewer':False,'errors':[], 'saved_bodies':[], 'run_ids':['R0','R1','R2','R3','P2']}
    page.on('pageerror',lambda error:state['errors'].append(str(error)))
    def route_api(route):
        path=urlsplit(route.request.url).path
        query=parse_qs(urlsplit(route.request.url).query)
        if path=='/api/me':
            result={'user_id':'U1','role':'viewer' if state['viewer'] else 'operator',
                    'permissions':[] if state['viewer'] else ['results.comparison.write']}
        elif path=='/api/runs':
            result={'items':[service.results.get_run_detail(rid,actor_user_id='U1') for rid in state['run_ids']]}
        elif path.startswith('/api/runs/'):
            parts = path.split('/')
            rid = parts[3]
            getter = {'situation':service.results.get_snapshot_situation,
                      'solution':service.results.get_solution, 'metrics':service.results.get_metrics}.get(parts[4] if len(parts) > 4 else '', service.results.get_run_detail)
            result = getter(rid, actor_user_id='U1')
        elif path.endswith('/damage-candidates'):
            result={'items':[{'r0_run_id':'R0','r1_run_id':'R1','r2_run_id':'R2'}]}
        elif path.endswith('/comparable-runs'):
            result=service.results.list_comparable_successful(query['base_run_id'][0],mode=query['mode'][0],actor_user_id='U1')
        elif path.endswith('/comparison-records'):
            assert route.request.headers.get('x-csrf-token') == 'browser-test-csrf'
            state['held_saves'].append(route)
            state['saved_bodies'].append(route.request.post_data_json)
            return
        elif path.endswith(('/damage-comparison','/scenario-comparison','/config-comparison')):
            mode='damage_effect' if path.endswith('/damage-comparison') else 'multi_scenario' if path.endswith('/scenario-comparison') else 'configuration'
            result=service.preview(mode,route.request.post_data_json,principal=OWNER)
            if state['strip_provenance']:
                result.pop('save_context')
            if state['ineligible']:
                result['eligible']=False
            if state['delay_comparison']:
                state['held_comparisons'].append((route,result))
                page.evaluate('(count) => { globalThis.heldComparisonCount = count; }', len(state['held_comparisons']))
                return
        else:
            result=api_payload(path)
        route.fulfill(status=200,json=result)
    page.route('**/api/**',route_api)
    state['open']=lambda:page.goto(server_url+'/results')
    yield state
    assert not state['errors']
    context.close()


def save(ui):
    page=ui['page']
    with page.expect_request('**/api/results/comparison-records'):
        page.locator('#saveComparisonButton').click()
    playwright.expect(page.locator('#saveComparisonButton')).to_be_disabled()


def release_save(ui,index=0):
    route=ui['held_saves'].pop(index)
    saved,created=ui['service'].save(route.request.post_data_json,principal=OWNER)
    route.fulfill(status=201 if created else 200,json={'record':saved,'created':created})


def ready(ui):
    ui['open']()
    initialized(ui['page'])
    playwright.expect(ui['page'].locator('#saveComparisonButton')).to_be_enabled()


def test_damage_stage_capture_and_late_response_does_not_disable_new_stage(ui):
    ready(ui)
    save(ui)
    page=ui['page']
    page.locator('[data-damage-stage="cluster"]').click()
    playwright.expect(page.locator('#saveComparisonButton')).to_be_enabled()
    assert ui['saved_bodies'][0]['comparison_mode']=='damage_effect'
    release_save(ui)
    playwright.expect(page.locator('#comparisonSaveStatus')).to_contain_text('R0 → R1')
    playwright.expect(page.locator('#saveComparisonButton')).to_have_text('保存比较记录 · 组群调整')
    save(ui)
    assert ui['saved_bodies'][1]['comparison_mode']=='cluster_effect'
    release_save(ui)
    playwright.expect(page.locator('#comparisonSaveStatus')).to_contain_text('R1 → R2')


def test_workspace_switch_and_history_retains_saved_pair(ui):
    ready(ui)
    save(ui)
    page=ui['page']
    page.locator('[data-workspace="multi"]').click()
    playwright.expect(page.locator('#saveComparisonButton')).to_be_enabled()
    relation=page.locator('#focusRelation').inner_text()
    release_save(ui)
    playwright.expect(page.locator('[data-workspace="multi"]')).to_have_class('active')
    assert page.locator('#focusRelation').inner_text()==relation
    playwright.expect(page.locator('#saveComparisonButton')).to_be_enabled()


def test_focus_loading_old_save_and_out_of_order_comparison_response(ui):
    ready(ui)
    page=ui['page']
    page.locator('[data-workspace="multi"]').click()
    playwright.expect(page.locator('#saveComparisonButton')).to_be_enabled()
    page.locator('#focusComparisonRun').select_option('R1')
    playwright.expect(page.locator('#saveComparisonButton')).to_be_enabled()
    save(ui)
    ui['delay_comparison']=True
    with page.expect_request('**/api/results/scenario-comparison'):
        page.locator('#focusComparisonRun').select_option('R3')
    playwright.expect(page.locator('#saveComparisonButton')).to_be_disabled()
    release_save(ui)
    playwright.expect(page.locator('#comparisonSaveStatus')).to_contain_text('R0 → R1')
    playwright.expect(page.locator('#saveComparisonButton')).to_be_disabled()
    # A second focus request completes first; the first comparison response is stale.
    with page.expect_request('**/api/results/scenario-comparison'):
        page.locator('#focusBaselineRun').select_option('R1')
    page.wait_for_function('globalThis.heldComparisonCount === 2')
    newer,new_payload=ui['held_comparisons'].pop()
    newer.fulfill(status=200,json=new_payload)
    playwright.expect(page.locator('#saveComparisonButton')).to_be_enabled()
    older,old_payload=ui['held_comparisons'].pop()
    older.fulfill(status=200,json=old_payload)
    playwright.expect(page.locator('#focusBaselineRun')).to_have_value('R1')
    playwright.expect(page.locator('#focusComparisonRun')).to_have_value('R3')
    save(ui)
    assert ui['saved_bodies'][-1]['selection']['baseline_run_id']=='R1'
    assert ui['saved_bodies'][-1]['selection']['comparison_run_id']=='R3'
    release_save(ui)


def test_selection_change_during_save_captures_new_selection(ui):
    ready(ui)
    page=ui['page']
    page.locator('[data-workspace="multi"]').click()
    playwright.expect(page.locator('#saveComparisonButton')).to_be_enabled()
    save(ui)
    page.locator('#changeConditionButton').click()
    page.locator('input[data-run-id="R3"]').uncheck()
    page.locator('#applyResultsComparison').click()
    playwright.expect(page.locator('#resultsOverlay')).not_to_have_class('open')
    playwright.expect(page.locator('#saveComparisonButton')).to_be_enabled()
    release_save(ui)
    playwright.expect(page.locator('#saveComparisonButton')).to_be_enabled()
    save(ui)
    assert set(ui['saved_bodies'][0]['selection']['run_ids'])=={'R0','R1','R3'}
    assert ui['saved_bodies'][1]['selection']['run_ids']==['R0','R1']
    release_save(ui)


@pytest.mark.parametrize('flag',['strip_provenance','ineligible','viewer'])
def test_save_guards(ui,flag):
    ui[flag]=True
    ui['open']()
    initialized(ui['page'])
    playwright.expect(ui['page'].locator('#saveComparisonButton')).to_be_disabled()
    if flag=='viewer':
        playwright.expect(ui['page'].locator('#saveComparisonButton')).to_be_hidden()
    assert not ui['saved_bodies']


def test_configuration_j_incomparable_allows_save(ui):
    ready(ui)
    page=ui['page']
    page.locator('[data-workspace="configuration"]').click()
    playwright.expect(page.locator('#saveComparisonButton')).to_be_enabled()
    page.locator('#focusComparisonRun').select_option('P2')
    playwright.expect(page.locator('#saveComparisonButton')).to_be_enabled()
    save(ui)
    assert ui['saved_bodies'][0]['comparison_mode']=='configuration'
    release_save(ui)
    playwright.expect(page.locator('#comparisonSaveStatus')).to_contain_text('R1 → P2')


def test_read_only_history_and_keyboard_save_at_responsive_widths(ui):
    ready(ui)
    page=ui['page']
    button=page.locator('#saveComparisonButton')
    for width in [320,768,1024,1440]:
        page.set_viewport_size({'width':width,'height':1000})
        playwright.expect(button).to_be_visible()
        assert button.bounding_box()['width']>0
    button.focus()
    with page.expect_request('**/api/results/comparison-records'):
        page.keyboard.press('Enter')
    release_save(ui)
    playwright.expect(page.locator('#comparisonSaveStatus')).to_contain_text('R0 → R1')
    # Supply only a saved history response; the history panel never computes a pair.
    page.unroute('**/api/**')
    def history_routes(route):
        path=urlsplit(route.request.url).path
        if path.endswith('/comparisons'):
            result=ui['service'].list(principal=OWNER)
        else:
            result=api_payload(path)
        route.fulfill(status=200,json=result)
    page.route('**/api/**',history_routes)
    page.locator('[data-workspace="assessment"]').click()
    playwright.expect(page.locator('#assessmentComparisons')).to_contain_text('R0')
    playwright.expect(page.locator('#assessmentComparisons')).to_contain_text('R1')
    playwright.expect(page.locator('#assessmentComparisons')).to_contain_text('current')
    page.locator('.comparison-record-details > summary').click()
    playwright.expect(page.locator('.comparison-record-detail-body table')).to_be_visible()
    page.locator('.comparison-record-evidence > summary').first.click()
    playwright.expect(page.locator('.comparison-evidence').first).to_be_visible()
    assert page.locator('#assessmentComparisons button').count()==0
    assert page.locator('#assessmentComparisons a').count()==0
    playwright.expect(button).to_be_hidden()


def test_hierarchical_identity_and_no_candidate_d_labels_or_duplicate_schemes(ui):
    from tests.analysis.test_comparison import with_frozen_config, solve
    service = ui['service']
    runs = service.results.runs
    snapshot = with_frozen_config(service.results.snapshots.get('R1'), run_id='R1-REPEAT')
    runs.create_queued(snapshot=snapshot, owner_user_id='U1')
    runs.claim_running(snapshot.run_id)
    result, metrics = solve(snapshot)
    runs.save_success(snapshot.run_id, solution=result.solution.to_dict(), metrics=metrics)
    ui['run_ids'].insert(2, 'R1-REPEAT')
    ready(ui)
    page = ui['page']
    page.locator('[data-workspace="multi"]').click()
    playwright.expect(page.locator('#saveComparisonButton')).to_be_enabled()
    assert page.locator('#conditionFacts .identity-chip').all_text_contents() == ['D0','D1','D2']
    playwright.expect(page.locator('#conditionFacts')).to_contain_text('出动优先')
    playwright.expect(page.locator('#focusBaselineIdentity .run-label')).to_be_visible()
    option_texts = page.locator('#focusComparisonRun option').all_text_contents()
    assert any('R1' in value for value in option_texts)
    assert all('出动优先' not in value for value in option_texts)
    selected_ids = page.locator('#focusComparisonRun option').evaluate_all('(options) => options.map(option => option.value)')
    assert len(set(selected_ids) & {'R1','R1-REPEAT'}) == 1
    selected = 'R1' if 'R1' in selected_ids else 'R1-REPEAT'
    alternative = 'R1-REPEAT' if selected == 'R1' else 'R1'
    page.locator('#changeConditionButton').click()
    playwright.expect(page.locator('#resultsOverlay')).to_have_class('results-overlay open')
    assert page.locator('#resultsOverlay .identity-chip').count() == 0
    repeat = page.locator('.candidate-row').filter(has=page.locator(f'[data-run-id="{alternative}"]'))
    playwright.expect(repeat).to_contain_text('同配置重复运行')
    playwright.expect(repeat.locator('input')).to_be_disabled()
    playwright.expect(repeat.locator('.run-label>b')).to_have_text('Damage')
    page.locator(f'[data-run-id="{selected}"]').uncheck()
    playwright.expect(page.locator(f'[data-run-id="{alternative}"]')).to_be_enabled()
    page.locator(f'[data-run-id="{alternative}"]').check()
    playwright.expect(page.locator(f'[data-run-id="{selected}"]')).to_be_disabled()
    page.locator('#applyResultsComparison').click()
    playwright.expect(page.locator('#resultsOverlay')).not_to_have_class('results-overlay open')
    assert page.locator(f'#conditionFacts a[href="/runs/{alternative}"]').count() == 1
    assert alternative in page.locator(f'#conditionFacts a[href="/runs/{alternative}"]').get_attribute('title')
    assert page.locator('#conditionFacts .identity-chip').all_text_contents() == ['D0','D1','D2']
    assert not ui['errors']


def test_single_run_uses_shared_identity_and_header_fits_its_three_rows(ui, server_url):
    page = ui['page']
    page.goto(server_url + '/runs/R1')
    playwright.expect(page.locator('#singleRunContent')).to_be_visible()
    playwright.expect(page.locator('#runBadges .single-damage-status')).to_contain_text('Damage')
    playwright.expect(page.locator('#runBadges .single-cluster-meta')).to_contain_text('出动优先')
    playwright.expect(page.locator('#runBadges .single-run-id')).to_have_text('R1')
    label = page.locator('#runBadges .run-label')
    assert '时间未记录' not in label.inner_text()
    assert 'R1' in label.get_attribute('title')
    dimensions = page.locator('#runBadges').evaluate('(node) => ({height:node.clientHeight, content:node.scrollHeight})')
    assert dimensions['content'] <= dimensions['height']
    assert not ui['errors']
