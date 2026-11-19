@EndUserText.label: '승인 요청 Parameter - 결재선'
define abstract entity ZDEMO_A_APPROVE_LINE
{
  key DummyKey : abap.char(1);
      Approver : abap.char(12);
      _Head    : association to parent ZDEMO_A_APPROVE on $projection.DummyKey = _Head.DummyKey;
}
