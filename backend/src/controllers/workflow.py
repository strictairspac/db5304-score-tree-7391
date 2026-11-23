from starlette.responses import Response

from src.config.settings import engine
from src.models.tables import evidence
from src.services import offline
from src.services.workflow import found
from src.utils.encoding import canonical


def download(identity):
    with engine.connect() as conn:
        data = found(conn, evidence, identity)
    payload = dict(data)
    # New inspection slips carry a catalog fingerprint and ship the offline
    # verification material.  Older slips have no fingerprint; they download
    # exactly as before.  A material problem must never block the download.
    try:
        material = offline.material_from_report(payload.get('report'))
    except (ValueError, TypeError, KeyError):
        material = None
    if material is not None:
        payload['offline_material'] = material
    return Response(canonical(payload), media_type='application/json', headers={
        'Content-Disposition': f'attachment; filename="evidence-{identity}.json"'})
