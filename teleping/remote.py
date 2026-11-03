"""Authenticated realtime runner API; all diagnostic errors are fixed codes."""
import copy
import re
from urllib.parse import urlsplit

import httpx

from .crypto import decrypt, encrypt
from .reports import complete_round


class _RemoteFailure(RuntimeError):
    pass


class RevisionConflict(_RemoteFailure):
    def __init__(self):
        super().__init__('revision_conflict')


class RemoteService:
    def __init__(self, base_url, api_token, key, client=None):
        try:
            parsed = urlsplit(base_url)
            if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                    or parsed.password is not None or parsed.path not in ('', '/')
                    or parsed.query or parsed.fragment or parsed.port == 0
                    or any(c.isspace() for c in base_url)):
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError('realtime_url_invalid') from None
        if not api_token or '\r' in api_token or '\n' in api_token:
            raise ValueError('configuration_missing')
        self.base_url = base_url.rstrip('/')
        self.token = api_token
        self.key = key
        self.client = client or httpx.Client(timeout=30, follow_redirects=False)

    @staticmethod
    def _run_id(run_id):
        if not isinstance(run_id, str) or not re.fullmatch(r'[1-9][0-9]{0,29}-[1-9][0-9]{0,9}', run_id):
            raise ValueError('run_id_invalid')

    def _post(self, path, body):
        try:
            response = self.client.post(self.base_url + path, json=body,
                headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'}, follow_redirects=False)
            if response.status_code == 409:
                try:
                    conflict = response.json().get('code') == 'revision_conflict'
                except Exception:
                    conflict = False
                if conflict:
                    raise RevisionConflict()
                raise _RemoteFailure('realtime_conflict')
            if response.status_code == 401:
                raise _RemoteFailure('realtime_unauthorized')
            if not 200 <= response.status_code < 300:
                raise _RemoteFailure('realtime_request_failed')
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError
            return data
        except _RemoteFailure:
            raise
        except Exception:
            raise RuntimeError('realtime_request_failed') from None

    def _payload(self, data, purpose):
        try:
            value = decrypt(data['payload'].encode('utf-8'), self.key, purpose=purpose)
            if not isinstance(value, dict):
                raise ValueError
            return value
        except Exception:
            raise RuntimeError('realtime_payload_invalid') from None

    def setup(self):
        data = self._post('/api/setup', {})
        if data.get('ok') is not True:
            raise RuntimeError('realtime_payload_invalid')

    def claim(self, run_id, force=False):
        self._run_id(run_id)
        data = self._post('/api/claim', {'run_id': run_id, 'force': bool(force)})
        if data.get('should_check') is False:
            return None
        if data.get('should_check') is not True:
            raise RuntimeError('realtime_payload_invalid')
        plan = self._payload(data, 'plan:' + run_id)
        if plan.get('run_id') != run_id:
            raise RuntimeError('realtime_payload_invalid')
        return plan

    def context(self, plan):
        run_id = plan['run_id']
        self._run_id(run_id)
        lease = plan['lease_token']
        result = self._payload(self._post('/api/context', {'run_id':run_id, 'lease_token':lease}), 'context:' + run_id)
        if result.get('run_id') != run_id or result.get('lease_token') != lease:
            raise RuntimeError('realtime_payload_invalid')
        return result

    def commit(self, plan, context, projection, cache_updates, messages):
        run_id = plan['run_id']
        self._run_id(run_id)
        payload = {'run_id':run_id, 'lease_token':plan['lease_token'], 'revision':context['revision'],
                   'site_states':projection['site_states'], 'expiry_cache_updates':cache_updates,
                   'summary':projection['last_summary'], 'messages':messages,
                   'pending_targets':projection['pending_targets']}
        data = self._post('/api/commit', {'run_id':run_id, 'payload':encrypt(payload, self.key, purpose='commit:' + run_id).decode('utf-8')})
        if data.get('ok') is not True:
            raise RuntimeError('realtime_payload_invalid')


def finish_remote(service, plan, results, cache_updates, *, now):
    for attempt in range(4):
        context = service.context(plan)
        expected = context['expected_targets']
        allowed = set(expected) & set(plan['expected_targets'])
        expected = [target for target in expected if target in allowed]
        projection = copy.deepcopy(context['state'])
        filtered = [item for item in results if isinstance(item, dict) and item.get('target') in allowed]
        cache = {target: value for target, value in cache_updates.items() if target in allowed}
        messages = complete_round(projection, filtered, expected_targets=expected, started=context['started'], now=now, round_id=plan['run_id'])
        try:
            service.commit(plan, context, projection, cache, messages)
            return len(expected), len(filtered)
        except RevisionConflict:
            if attempt == 3:
                raise
