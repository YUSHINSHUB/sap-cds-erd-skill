@AccessControl.authorizationCheck: #NOT_REQUIRED
@EndUserText.label: '주문별 가장 최근 승인'
define view entity ZDEMO_I_ORDER_APPR_LAST
  as select from ZDEMO_I_ORDER_APPR
{
  key OrderId,

      max( ApprovalNo ) as LastApprovalNo
}
group by
  OrderId
