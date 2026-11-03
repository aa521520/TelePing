import json

import httpx
import pytest

from teleping.crypto import decrypt, encrypt, generate_key


def test_remote_wire_and_purpose():
    from teleping.remote import RemoteService
    key = generate_key()
    plan = {'run_id': '123-1', 'lease_token': 'lease', 'batches': [], 'expected_targets': []}
    def handler(request):
        assert request.url.host == 'service.example'
        assert request.headers['authorization'] == 'Bearer secret-canary'
        body = json.loads(request.content)
        if request.url.path == '/api/claim':
            assert body == {'run_id': '123-1', 'force': True}
            return httpx.Response(200, json={'should_check': True, 'payload': encrypt(plan, key, purpose='plan:123-1').decode()})
        assert request.url.path == '/api/context'
        assert body == {'run_id': '123-1', 'lease_token': 'lease'}
        return httpx.Response(200, json={'payload': encrypt({'run_id': '123-1', 'lease_token': 'lease', 'revision': 1, 'expected_targets': [], 'state': {}}, key, purpose='context:123-1').decode()})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = RemoteService('https://service.example', 'secret-canary', key, client)
    assert service.claim('123-1', True) == plan
    assert service.context(plan)['revision'] == 1
    with pytest.raises(ValueError):
        RemoteService('http://service.example', 'secret', key, client)
    with pytest.raises(ValueError):
        RemoteService('https://service.example/path', 'secret', key, client)


def test_finish_recomputes_after_conflict_and_excludes_cancelled_targets():
    from teleping.remote import RemoteService, finish_remote
    key = generate_key()
    plan = {'run_id':'123-1', 'lease_token':'l', 'started':100, 'expected_targets':['old', 'temp'], 'batches':[['old', 'temp']]}
    commits = []
    def handler(request):
        body = json.loads(request.content)
        if request.url.path == '/api/context':
            valid = ['old', 'temp'] if not commits else ['temp']
            context = {'run_id':'123-1','lease_token':'l','revision':len(commits)+1,'started':100,'expected_targets':valid,'state':{'targets':['new'], 'site_states':{}, 'pending_targets':[], 'last_summary':{}}}
            return httpx.Response(200, json={'payload': encrypt(context,key,purpose='context:123-1').decode()})
        payload = decrypt(body['payload'].encode(),key,purpose='commit:123-1')
        commits.append(payload)
        return httpx.Response(409, json={'code':'revision_conflict'}) if len(commits)==1 else httpx.Response(200,json={'ok':True})
    service = RemoteService('https://service.example', 'secret', key, httpx.Client(transport=httpx.MockTransport(handler)))
    finish_remote(service, plan, [{'target':'old','status':'failed'}], {'old': {'x':1}, 'temp': {'x':2}}, now=200)
    assert commits[1]['revision'] == 2
    assert set(commits[1]['site_states']) == {'temp'}
    assert commits[1]['pending_targets'] == ['temp']
    assert commits[1]['summary']['unmeasured'] == 1
    assert commits[1]['expiry_cache_updates'] == {'temp': {'x':2}}
    assert 'targets' not in commits[1]


def test_conflict_retry_is_bounded_and_other_errors_are_sanitized():
    from teleping.remote import RemoteService, RevisionConflict, finish_remote
    key = generate_key()
    plan = {'run_id':'123-1', 'lease_token':'l', 'started':1, 'expected_targets':[], 'batches':[]}
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path == '/api/context':
            context = {'run_id':'123-1','lease_token':'l','revision':1,'started':1,'expected_targets':[], 'state':{'targets':[], 'site_states':{}, 'pending_targets':[], 'last_summary':{}}}
            return httpx.Response(200, json={'payload':encrypt(context,key,purpose='context:123-1').decode()})
        return httpx.Response(409,json={'code':'revision_conflict'})
    service = RemoteService('https://service.example', 'secret', key, httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(RevisionConflict):
        finish_remote(service, plan, [], {}, now=2)
    assert calls.count('/api/context') == calls.count('/api/commit') == 4
    def unauthorized(request):
        return httpx.Response(401, text='private-canary secret')
    service = RemoteService('https://service.example', 'secret', key, httpx.Client(transport=httpx.MockTransport(unauthorized)))
    with pytest.raises(RuntimeError, match='^realtime_unauthorized$'):
        service.claim('123-1')


def test_wrong_encryption_purpose_is_rejected():
    from teleping.remote import RemoteService
    key = generate_key()
    def handler(request):
        return httpx.Response(200,json={'should_check':True,'payload':encrypt({'run_id':'123-1'},key,purpose='context:123-1').decode()})
    service = RemoteService('https://service.example','secret',key,httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(RuntimeError, match='^realtime_payload_invalid$'):
        service.claim('123-1')


def test_transport_exception_is_never_exposed():
    from teleping.remote import RemoteService
    def handler(request):
        raise RuntimeError('private-canary credential')
    service = RemoteService('https://service.example','secret',generate_key(),httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(RuntimeError, match='^realtime_request_failed$'):
        service.claim('123-1')


@pytest.mark.parametrize('run_id', ['r', '1', '0-1', '1-0', '01-1', '1-01', '1_1', '1-1-1', '9' * 31 + '-1', '1-' + '9' * 11])
def test_invalid_actions_run_ids_are_rejected_without_network(run_id):
    from teleping.remote import RemoteService
    def handler(request):
        raise AssertionError('unexpected network')
    service = RemoteService('https://service.example', 'secret', generate_key(), httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ValueError, match='^run_id_invalid$'):
        service.claim(run_id)


def test_setup_uses_authenticated_post_and_sanitizes_errors():
    from teleping.remote import RemoteService
    def handler(request):
        assert request.url.path == '/api/setup'
        assert request.headers['authorization'] == 'Bearer secret'
        assert json.loads(request.content) == {}
        return httpx.Response(200, json={'ok': True})
    service = RemoteService('https://service.example', 'secret', generate_key(), httpx.Client(transport=httpx.MockTransport(handler)))
    assert service.setup() is None
    def fail(request):
        raise RuntimeError('private-canary')
    service = RemoteService('https://service.example', 'secret', generate_key(), httpx.Client(transport=httpx.MockTransport(fail)))
    with pytest.raises(RuntimeError, match='^realtime_request_failed$'):
        service.setup()
