"""Account-only transport for author-private character workflows."""

from dataclasses import asdict
import json

from flask import Blueprint, jsonify, request, url_for

from core import auth
from core.character_workflows.capabilities import capabilities_for
from core.character_workflows.drafts import authorize_personal, key_hash
from core.character_workflows.identity import resolve_character
from core.character_workflows.types import DraftInput, WorkflowError


def error_response(error):
    return jsonify(error={'code': error.code, 'message': error.message,
                          'issues': list(error.issues)}), error.status


def _body():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise WorkflowError('invalid_workflow_request', 'Send a JSON object.', 422)
    return body


def _inputs(value):
    try:
        if not isinstance(value, dict):
            raise TypeError()
        return DraftInput(**value)
    except TypeError:
        raise WorkflowError('invalid_draft_input', 'Check the draft input format.', 422) from None


def create_blueprint(*, drafts, workflows, resolve_context, render_sheet):
    bp = Blueprint('character_workflows', __name__)
    bp.register_error_handler(WorkflowError, error_response)

    @bp.before_request
    def require_csrf():
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and not auth.check_csrf():
            raise WorkflowError('csrf_failed', 'Refresh this page before saving.', 400)

    def creation_status(context, request_key):
        with drafts.store.transaction(context) as tx:
            fresh = tx.fresh_context()
            authorize_personal(fresh)
            prior = tx.get_receipt(fresh.principal.user_id, 'create_draft', key_hash(request_key))
            return 200 if prior else 201

    @bp.get('/api/character-workflows/drafts')
    def draft_list():
        return jsonify(drafts=[asdict(d) for d in drafts.list(resolve_context())])

    @bp.post('/api/character-workflows/drafts')
    def draft_create():
        body, context = _body(), resolve_context()
        status = creation_status(context, body.get('request_key'))
        draft = drafts.create(context, _inputs(body.get('inputs')),
                              request_key=body.get('request_key'), target_id=body.get('target_id'))
        return jsonify(draft=asdict(draft)), status

    @bp.get('/api/character-workflows/drafts/<draft_id>')
    def draft_get(draft_id):
        return jsonify(draft=asdict(drafts.get(resolve_context(), draft_id)))

    @bp.patch('/api/character-workflows/drafts/<draft_id>')
    def draft_save(draft_id):
        body = _body()
        return jsonify(draft=asdict(drafts.save(resolve_context(), draft_id,
            _inputs(body.get('inputs')), expected_revision=body.get('expected_revision'))))

    @bp.post('/api/character-workflows/drafts/<draft_id>/discard')
    def draft_discard(draft_id):
        drafts.discard(resolve_context(), draft_id, expected_revision=_body().get('expected_revision'))
        return jsonify(ok=True)

    @bp.post('/api/character-workflows/drafts/<draft_id>/copy')
    def draft_copy(draft_id):
        body, context = _body(), resolve_context()
        status = creation_status(context, body.get('request_key'))
        draft = drafts.copy(context, draft_id, _inputs(body.get('inputs')),
                            request_key=body.get('request_key'))
        return jsonify(draft=asdict(draft)), status

    @bp.post('/api/character-workflows/drafts/<draft_id>/publish')
    def draft_publish(draft_id):
        body = _body()
        result = workflows.publish(resolve_context(), draft_id,
            expected_revision=body.get('expected_revision'), request_key=body.get('request_key'),
            force=body.get('force', False))
        return jsonify(**asdict(result), url=url_for('.character_sheet', character_id=result.character_id))

    @bp.post('/api/character-workflows/imports')
    def import_prepare():
        context = resolve_context()
        upload = request.files.get('file')
        if upload is not None:
            body = request.form
            content, filename = upload.read(2_097_153), upload.filename or 'character.pdf'
        else:
            body = _body()
            source = body.get('source')
            if not isinstance(source, dict):
                raise WorkflowError('invalid_import', 'Choose a character JSON object or PDF file.', 422)
            try:
                content = json.dumps(source, ensure_ascii=False, allow_nan=False).encode('utf-8')
            except (TypeError, ValueError, UnicodeError):
                raise WorkflowError('invalid_import', 'Choose a valid character JSON object.', 422) from None
            filename = 'character.json'
        status = creation_status(context, body.get('request_key'))
        draft = drafts.prepare_import(context, content, filename,
            request_key=body.get('request_key'), target_id=body.get('target_id') or None)
        return jsonify(draft=asdict(draft)), status

    @bp.get('/characters/<character_id>')
    def character_sheet(character_id):
        context = resolve_context()
        character = resolve_character(context.campaign_id, character_id)
        capabilities = capabilities_for(context, character)
        if not capabilities.view:
            raise WorkflowError('character_not_found', 'Character not found.', 404)
        return render_sheet(character, capabilities)

    return bp
