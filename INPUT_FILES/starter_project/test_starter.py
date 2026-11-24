import unittest, sqlite3, pathlib, json, sys
sys.path.insert(0,str(pathlib.Path(__file__).parent))
from listener import lookup_transaction
BASE=pathlib.Path(__file__).resolve().parent.parent
class StarterChecks(unittest.TestCase):
 def test_database(self):
  with sqlite3.connect(BASE/'transactions.sqlite3') as db:
   self.assertEqual(db.execute('SELECT COUNT(*) FROM transactions').fetchone()[0],16)
 def test_lookup(self):
  self.assertEqual(lookup_transaction('TXN-0001')['status'],'success')
  self.assertIsNone(lookup_transaction('TXN-9999'))
 def test_fixtures(self):
  names=list((BASE/'fixtures').glob('event_*.json'))
  self.assertEqual(len(names),8)
  for x in names:self.assertIsInstance(json.loads(x.read_text()),dict)
if __name__=='__main__':unittest.main()
