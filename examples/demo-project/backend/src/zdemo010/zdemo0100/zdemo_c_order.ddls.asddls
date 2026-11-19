@AccessControl.authorizationCheck: #NOT_REQUIRED
@EndUserText.label: '주문 목록'
@Metadata.allowExtensions: true
define root view entity ZDEMO_C_ORDER
  provider contract transactional_query
  as projection on ZDEMO_R_ORDER
{
  key OrderId,
      Customer,
      CustomerName,
      OrderDate,
      Status,
      TotalAmount,
      Currency,
      LastApprovalNo,

      _Item : redirected to composition child ZDEMO_C_ORDER_ITEM
}
