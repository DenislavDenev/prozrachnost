import copy
import json
from pathlib import Path
import pytest
from ingest.pdf import inspection_index
from ingest.parse import ShapeError
F=Path(__file__).parent/'fixtures'

@pytest.mark.parametrize('file,count',[('adfi-2024q2-table.json',173),('adfi-2011q3-table.json',87)])
def test_original_index_tables(file,count):
    d=json.loads((F/file).read_text(encoding='utf-8'))
    rows=inspection_index(d['tables'],d['document'],'source-fixture-hash')
    assert len(rows)==count
    assert rows[0]['source_row']==1 and rows[-1]['source_row']==count
    assert rows[0]['completed_on'] is None and rows[0]['eik'] is None
    assert rows[0]['document_page']==1
    if count==173:
        assert rows[0]['auditee']=='139-то основно училище /ОУ/ "Захарий Круша" - София'
        assert rows[7]['auditee']==rows[8]['auditee'] and rows[7]['id']!=rows[8]['id']

def test_truncated_and_sequence_guard():
    d=json.loads((F/'adfi-2024q2-table.json').read_text(encoding='utf-8'))
    tables=copy.deepcopy(d['tables']);tables[0][1][2][0]='9'
    with pytest.raises(ShapeError,match='номер'):
        inspection_index(tables,d['document'],'source-hash')
    tables=copy.deepcopy(d['tables']);tables[0][1][0].append('unknown column')
    with pytest.raises(ShapeError):
        inspection_index(tables,d['document'],'source-hash')
