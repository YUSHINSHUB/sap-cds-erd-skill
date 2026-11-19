@AccessControl.authorizationCheck: #NOT_REQUIRED
@EndUserText.label: '주문 승인 이력'
define view entity ZDEMO_I_ORDER_APPR
  as select from ztdemo_appr
{
  key order_id    as OrderId,
  key approval_no as ApprovalNo,
      approver    as Approver,
      status      as ApprovalStatus,
      approved_at as ApprovedAt
}
