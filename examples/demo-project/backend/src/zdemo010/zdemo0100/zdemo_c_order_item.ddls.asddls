@AccessControl.authorizationCheck: #NOT_REQUIRED
@EndUserText.label: '주문 품목'
define view entity ZDEMO_C_ORDER_ITEM
  as projection on ZDEMO_R_ORDER_ITEM
{
  key OrderId,
  key ItemNo,
      Product,
      Amount,
      Currency,
      IsCanceled,

      _Order : redirected to parent ZDEMO_C_ORDER
}
