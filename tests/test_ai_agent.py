from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import pandas as pd
from pandas.testing import assert_frame_equal
from ai_agent import execute_scenario, run_procurement_agent
from demo_data import load_demo_data


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.data=load_demo_data()
        self.params={'as_of':'2026-09-22','lead_time_days':30,'review_days':30,'safety_days':14,'growth_pct':0}

    def test_offline_delay_uses_tool_and_preserves_original(self):
        before={key:value.copy(deep=True) for key,value in self.data.items() if isinstance(value,pd.DataFrame)}
        with patch('ai_agent._request_response') as api:
            result=run_procurement_agent('Поставка ИЭК задержится на 10 дней',self.data,self.params)
        api.assert_not_called()
        self.assertEqual(result['mode'],'local')
        self.assertEqual(result['trace'][0]['инструмент'],'compare_supply_delay')
        self.assertEqual(result['trace'][0]['параметры'],{'supplier':'ИЭК','delay_days':10})
        self.assertEqual(set(result['comparison']['Поставщик']),{'ИЭК'})
        self.assertTrue(result['comparison']['Потребность после'].ge(result['comparison']['Потребность до']).all())
        for key,frame in before.items():
            assert_frame_equal(frame,self.data[key])

    def test_growth_and_quality_tools(self):
        result=run_procurement_agent('Рост спроса на 20%',self.data,self.params)
        self.assertEqual(result['trace'][0]['инструмент'],'compare_demand_growth')
        self.assertGreater(result['comparison']['Потребность после'].sum(),result['comparison']['Потребность до'].sum())
        result=run_procurement_agent('Проверь качество данных',self.data,self.params)
        self.assertEqual(result['trace'][0]['инструмент'],'inspect_data_quality')

    def test_invalid_tool_parameters_rejected(self):
        for name,args in [('send_order',{'supplier':'ИЭК'}),('compare_supply_delay',{'supplier':'ИЭК','delay_days':-1}),
                          ('compare_supply_delay',{'supplier':'ИЭК','delay_days':True}),('inspect_data_quality',{'supplier':'unknown'})]:
            with self.subTest(name=name,args=args), self.assertRaises(ValueError):
                execute_scenario(name,args,self.data,self.params)

    def test_missing_credentials_does_not_call_api(self):
        with patch('ai_agent._request_response') as api:
            result=run_procurement_agent('Проверь качество данных',self.data,self.params,use_api=True)
        api.assert_not_called()
        self.assertEqual(result['mode'],'not_configured')

    def test_actual_function_loop_passes_only_aggregate_data(self):
        self.data['sales']['name']='PRIVATE_PRODUCT_DESCRIPTION'
        self.data['transactions']=pd.DataFrame([['ИЭК','DEMO-001',pd.Timestamp('2026-06-01'),10,'PRIVATE_CLIENT']],
                                              columns=['supplier','sku','date','qty','customer_id'])
        requests=[]
        responses=[{'output':[{'type':'reasoning','id':'r1','summary':[]},
                              {'type':'function_call','name':'compare_supply_delay','call_id':'call1',
                               'arguments':json.dumps({'supplier':'ИЭК','delay_days':10})}]},
                   {'output':[{'type':'message','content':[{'type':'output_text','text':'Результат проверенного сценария.'}]}]}]
        def fake_api(payload,key):
            requests.append(deepcopy(payload))
            return responses.pop(0)
        with patch('ai_agent._request_response',side_effect=fake_api):
            result=run_procurement_agent('Поставка ИЭК задержится на 10 дней',self.data,self.params,True,'test-key','test-model')
        self.assertEqual(result['mode'],'openai')
        self.assertEqual(len(result['trace']),1)
        self.assertTrue(any(item['type']=='function_call_output' for item in requests[1]['input'] if 'type' in item))
        self.assertTrue(any(item.get('id')=='r1' for item in requests[1]['input']))
        sent=json.dumps(requests,ensure_ascii=False)
        self.assertNotIn('PRIVATE_PRODUCT_DESCRIPTION',sent)
        self.assertNotIn('PRIVATE_CLIENT',sent)
        self.assertNotIn('DEMO-001',sent)
        self.assertFalse(requests[0]['store'])

    def test_model_cannot_change_explicit_delay(self):
        responses=[{'output':[{'type':'function_call','name':'compare_supply_delay','call_id':'x',
                              'arguments':json.dumps({'supplier':'ИЭК','delay_days':90})}]},
                   {'output':[{'type':'message','content':[{'type':'output_text','text':'wrong'}]}]}]
        with patch('ai_agent._request_response',side_effect=responses):
            result=run_procurement_agent('Поставка ИЭК задержится на 10 дней',self.data,self.params,True,'key','model')
        self.assertEqual(result['mode'],'fallback')
        self.assertEqual(result['trace'][-1]['параметры']['delay_days'],10)

    def test_api_error_falls_back_without_exposing_secret(self):
        with patch('ai_agent._request_response',side_effect=RuntimeError('secret-key PRIVATE_RESPONSE')):
            result=run_procurement_agent('Проверь качество данных',self.data,self.params,True,'secret-key','model')
        self.assertEqual(result['mode'],'fallback')
        self.assertNotIn('secret-key',str(result))
        self.assertNotIn('PRIVATE_RESPONSE',str(result))

    def test_free_question_offline_does_not_invent_action(self):
        result=run_procurement_agent('Привет!',self.data,self.params)
        self.assertEqual(result['trace'],[])
        self.assertTrue(result['comparison'].empty)


if __name__=='__main__':
    unittest.main()
