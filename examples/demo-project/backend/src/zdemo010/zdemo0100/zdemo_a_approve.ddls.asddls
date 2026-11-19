@EndUserText.label: '승인 요청 Parameter - Head'
define root abstract entity ZDEMO_A_APPROVE
{
  key DummyKey : abap.char(1);
      Comment  : abap.char(255);
      _Line    : composition [1..*] of ZDEMO_A_APPROVE_LINE;
}
