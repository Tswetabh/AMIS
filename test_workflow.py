import os, tempfile, unittest

sandbox = tempfile.TemporaryDirectory()
os.environ['AMIS_DATA'] = sandbox.name
from app import app

class Workflow(unittest.TestCase):
    def test_farmer_to_buyer(self):
        app.testing=True
        client=app.test_client()
        def post(path, **fields):
            with client.session_transaction() as s: fields['csrf']=s['csrf']
            return client.post(path,data=fields,follow_redirects=True)
        client.get('/')
        self.assertIn(b'Update comparison',post('/register',name='grower',password='test-pass-123',role='Farmer').data)
        r=post('/profile',town='Indore',crop='Wheat',kg='500')
        self.assertIn(b'9,250.00',r.data)
        self.assertIn(b'10,250.00',r.data)
        self.assertIn(b'Market choice saved',post('/confirm',town='Indore').data)
        self.assertIn(b'Lot published',post('/lots',crop='Wheat',town='Indore',kg='500',price='2100',grade='A').data)
        post('/logout'); post('/register',name='buyer',password='test-pass-456',role='Buyer')
        self.assertIn(b'grower',client.get('/lots?crop=Wheat&town=Indore').data)
        self.assertNotIn(b'grower',client.get('/lots?crop=Maize').data)
        self.assertEqual(client.get('/reports').status_code,403)
