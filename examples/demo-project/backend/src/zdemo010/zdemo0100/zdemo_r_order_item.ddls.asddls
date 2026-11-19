@AccessControl.authorizationCheck: #NOT_REQUIRED
@EndUserText.label: '주문 품목 (BO)'
define view entity ZDEMO_R_ORDER_ITEM
  as select from ZDEMO_I_ORDER_ITEM
  association to parent ZDEMO_R_ORDER as _Order on $projection.OrderId = _Order.OrderId
{
  key OrderId,
  key ItemNo,
      Product,
      @Semantics.amount.currencyCode: 'Currency'
      Amount,
      Currency,
      IsCanceled,

      _Order
}
