import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lumenflow/scripts'))
from _models import validate_models_response
from _canvas import validate_canvas_response
from _contract import LumenflowError

class ModelsTest(unittest.TestCase):
    def setUp(self):
        self.delivery={'generation_resolution':'480p','delivery_resolution':'720p','enhancement_included':True}
        self.model={'selection_id':'a'*64,'name':'Seedance 2.0 Subsidized','kind':'video','duration':{'min':4,'max':15},'pricing':{'unit_credits':2.5,'billing_unit':'per_second'},'delivery':self.delivery}
    def test_closed_model_projection(self):
        value={'project_id':'prj_test','models':[self.model]}
        validate_models_response('/projects/prj_test/models',value)
        for key in ('provider','channel','config','debug','model_id'):
            self.model[key]='private'
            with self.assertRaises(LumenflowError):validate_models_response('/projects/prj_test/models',value)
            del self.model[key]
        self.delivery['internal']='private'
        with self.assertRaises(LumenflowError):validate_models_response('/projects/prj_test/models',value)
    def test_quote_decimal_cost_and_nested_injection(self):
        value={'project_id':'prj_test','selection_id':'a'*64,'total_credits':10.5,'expires_at':1800000120,'quote_fingerprint':'spq1.1800000120.'+'a'*64,'scene_costs':[{'scene_id':'scn_test','credits':10.5}],'delivery':self.delivery}
        validate_models_response('/projects/prj_test/quotes',value)
        value['scene_costs'][0]['prompt']='private'
        with self.assertRaises(LumenflowError):validate_models_response('/projects/prj_test/quotes',value)
    def test_canvas_enhancement_shape(self):
        value={'canvas_id':'cvs_test','job_id':'cgj_test','node_id':'node_video','status':'failed','enhancement':{'phase':'enhancement_failed','refunded_credits':2.5}}
        validate_canvas_response('/canvases/cvs_test/jobs/cgj_test',value)
        value['enhancement']['provider_error']='private'
        with self.assertRaises(LumenflowError):validate_canvas_response('/canvases/cvs_test/jobs/cgj_test',value)

class SelectedModelTransportTest(unittest.TestCase):
    def test_selected_model_transport_and_missing_quote(self):
        from test_client import FakeOpener, FakeResponse, config
        from _client import LumenflowClient
        import json
        opener=FakeOpener([FakeResponse({'project_id':'prj_test','status':'generating','estimated_credits':2.5})])
        client=LumenflowClient(config(),opener=opener)
        with self.assertRaises(LumenflowError):client.generate('prj_test',selection_id='a'*64)
        self.assertEqual(opener.requests,[])
        client.generate('prj_test',selection_id='a'*64,quote_fingerprint='spq1.1800000120.'+'b'*64,scene_ids=['scn_test'],idempotency_key='selected-model-test')
        request=opener.requests[0]
        self.assertEqual(json.loads(request.data),{'mode':'selected','scene_ids':['scn_test'],'selection_id':'a'*64,'quote_fingerprint':'spq1.1800000120.'+'b'*64})
        self.assertNotIn('model_id',json.loads(request.data))
    def test_cli_requires_explicit_confirmation(self):
        import lumenflow as cli
        from test_cli import FakeClient
        args=cli.build_parser().parse_args(['generate','prj_test','--idempotency-key','test-model-key','--selection-id','a'*64,'--quote-fingerprint','spq1.1800000120.'+'b'*64])
        client=FakeClient()
        with self.assertRaises(LumenflowError):cli.execute(args,client)
        self.assertEqual(client.calls,[])
        args.confirm_generation=True
        cli.execute(args,client)
        self.assertEqual(client.calls[0][0],'generate')
