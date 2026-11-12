from __future__ import annotations
import argparse
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.request import Request
SCRIPTS=Path(__file__).parents[1]/"lumenflow"/"scripts"
sys.path.insert(0,str(SCRIPTS))
from _canvas import CanvasClient, validate_canvas_response, validate_upload_url, UPLOAD_HOST, NoRedirect, execute_canvas
from _contract import LumenflowError

class Response:
    def read(self, size=-1): return b'{"object_key":"PRIVATE_WORKER_DATA"}'
    def __enter__(self): return self
    def __exit__(self,*args): pass
class Client:
    def __init__(self,responses):
        self.responses=list(responses);self.calls=[];self.config=SimpleNamespace(base_url="https://api.example/open/v1",api_key="PRIVATE_PAT")
    def _request(self,method,path,**kwargs):
        self.calls.append((method,path,kwargs));return self.responses.pop(0)
    def download(self,url,destination,**kwargs):
        self.calls.append(("DOWNLOAD",url,kwargs));return destination
class CanvasTests(unittest.TestCase):
    def test_structure_rejects_body_and_all_internal_metadata(self):
        for node in ({"node_id":"node_1","text":"PRIVATE"},{"node_id":"node_1","capabilitySnapshot":{}},
                     {"node_id":"node_1","parameters":{"custom":"PRIVATE"}}, {"node_id":{"unexpected":"PRIVATE"}}):
            with self.assertRaises(LumenflowError): validate_canvas_response("/canvases/cnv_1",{"nodes":[node]})
        validate_canvas_response("/canvases/cnv_1/content",{"nodes":[{"node_id":"node_1","type":"text","revision":0,"text":"business text"}]})
    def test_closed_models_jobs_and_tickets(self):
        for path,data in (("/canvases/cnv_1/models",{"models":[{"selection_id":"a","description":"PRIVATE"}]}),
                          ("/canvases/cnv_1/jobs/job_1",{"parameters":{}}),
                          ("/canvases/cnv_1/uploads/upl_1",{"transport":{"upload_url":f"https://{UPLOAD_HOST}/upload/canvas-media","upload_token":"PRIVATE","expires_at":1}})):
            with self.assertRaises(LumenflowError):validate_canvas_response(path,data)
    def test_wrong_host_and_redirect_are_refused(self):
        for url in ("https://evil.example/upload/canvas-media",f"https://{UPLOAD_HOST}/wrong",f"https://user@{UPLOAD_HOST}/upload/canvas-media",f"https://{UPLOAD_HOST}/upload/canvas-media?q=secret"):
            with self.assertRaises(LumenflowError):validate_upload_url(url)
        with self.assertRaises(URLError):NoRedirect().redirect_request(Request("https://example.com"),None,302,"",{},"https://evil.example")
    def test_upload_uses_only_scoped_ticket_and_never_returns_worker_data(self):
        responses=[{"canvas_id":"cnv_1","upload_id":"upl_1","node_id":"node_1","status":"reserved","transport":{
            "upload_url":f"https://{UPLOAD_HOST}/upload/canvas-media","upload_token":"SCOPED_TICKET","expires_at":999}},
            {"canvas_id":"cnv_1","upload_id":"upl_1","node_id":"node_1","status":"succeeded","transport":None}]
        client=Client(responses);requests=[]
        class Opener:
            def open(self,request,**kwargs):
                requests.append(request)
                self_body=request.data.read()
                assert self_body==b"image-data"
                return Response()
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/"image.png";file.write_bytes(b"image-data")
            result=CanvasClient(client,upload_opener=Opener()).upload("cnv_1","node_1",1,file,"upload-key-123")
        self.assertEqual(requests[0].get_header("Authorization"),"Bearer SCOPED_TICKET")
        self.assertNotIn("PRIVATE",json.dumps(result));self.assertNotIn("SCOPED_TICKET",json.dumps(result));self.assertNotIn("transport",result)
    def test_upload_unknown_outcome_does_not_retry_or_create_another_upload(self):
        client=Client([{"upload_id":"upl_1","status":"reserved","transport":{"upload_url":f"https://{UPLOAD_HOST}/upload/canvas-media","upload_token":"SCOPED_TICKET","expires_at":999}}])
        class Opener:
            def open(self,*args,**kwargs):raise URLError("PRIVATE_WORKER_ERROR")
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/"image.png";file.write_bytes(b"data")
            with self.assertRaises(LumenflowError) as caught:CanvasClient(client,upload_opener=Opener()).upload("cnv_1","node_1",0,file,"upload-key-123")
        self.assertEqual(caught.exception.code,"upload_outcome_unknown");self.assertNotIn("PRIVATE",str(caught.exception));self.assertEqual(len(client.calls),1)
    def test_download_rejects_external_url_and_hides_bearer_grant(self):
        grant="a"*43;client=Client([{"asset_id":"med_1","download_url":f"/open/v1/canvas-downloads/{grant}","expires_at":1}])
        result=CanvasClient(client).download("cnv_1","med_1",Path("result.png"))
        self.assertNotIn(grant,json.dumps(result));self.assertEqual(client.calls[-1][0],"DOWNLOAD")
        client=Client([{"download_url":"https://evil.example/file"}])
        with self.assertRaises(LumenflowError):CanvasClient(client).download("cnv_1","med_1",Path("result.png"))
    def test_upload_validation_refusal_is_safe_and_never_retried(self):
        cases = [
            (422, b'{"error":"image_dimensions_invalid"}', "upload_rejected"),
            (502, b'{"error":"image_dimensions_invalid"}', "upload_outcome_unknown"),
            (422, b'{"error":"PRIVATE_WORKER_ERROR"}', "upload_outcome_unknown"),
            (422, b'{"error":{"secret":"PRIVATE"}}', "upload_outcome_unknown"),
            (422, b'{"error":"image_dimensions_invalid","debug":"PRIVATE"}', "upload_outcome_unknown"),
            (422, b'PRIVATE' * 1000, "upload_outcome_unknown"),
        ]
        for status, body, expected in cases:
            with self.subTest(status=status, expected=expected):
                client=Client([{"upload_id":"upl_1","status":"reserved","transport":{"upload_url":f"https://{UPLOAD_HOST}/upload/canvas-media","upload_token":"SCOPED_TICKET","expires_at":999}}])
                class Opener:
                    def open(self,*args,**kwargs):
                        raise HTTPError("https://PRIVATE", status, "PRIVATE", {}, io.BytesIO(body))
                with tempfile.TemporaryDirectory() as tmp:
                    file=Path(tmp)/"image.png";file.write_bytes(b"data")
                    with self.assertRaises(LumenflowError) as caught:
                        CanvasClient(client,upload_opener=Opener()).upload("cnv_1","node_1",0,file,"upload-key-123")
                self.assertEqual(caught.exception.code,expected)
                self.assertNotIn("PRIVATE",str(caught.exception))
                self.assertNotIn("SCOPED_TICKET",str(caught.exception))
                self.assertIn("upl_1",str(caught.exception))
                self.assertEqual(len(client.calls),1)
    def test_generation_requires_explicit_consent_before_any_request(self):
        client=Client([])
        with self.assertRaises(LumenflowError):execute_canvas(SimpleNamespace(command="canvas-generate",canvas_id="cnv_1",confirm_generation=False),client)
        self.assertEqual(client.calls,[])

if __name__=="__main__":unittest.main()
