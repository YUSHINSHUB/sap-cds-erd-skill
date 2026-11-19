@AccessControl.authorizationCheck: #NOT_REQUIRED
@EndUserText.label: '승인 이력 (최근 건)'
define view entity ZDEMO_C_ORDER_APPR_HIS
  as select from ZDEMO_I_ORDER_APPR as _Appr
    inner join   ZDEMO_I_ORDER      as _Order on _Order.OrderId = _Appr.OrderId
  association [0..1] to ZDEMO_I_ORDER_APPR_LAST as _Last on $projection.OrderId = _Last.OrderId
{
  key _Appr.OrderId,
  key _Appr.ApprovalNo,
      _Order.CustomerName,
      _Appr.Approver,
      _Appr.ApprovalStatus,
      _Appr.ApprovedAt,
      _Last.LastApprovalNo
}
where
  _Appr.ApprovalNo = _Last.LastApprovalNo
