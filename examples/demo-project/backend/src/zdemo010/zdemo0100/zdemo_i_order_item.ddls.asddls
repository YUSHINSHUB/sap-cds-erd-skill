@AccessControl.authorizationCheck: #NOT_REQUIRED
@EndUserText.label: '주문 품목'
define view entity ZDEMO_I_ORDER_ITEM
  as select from ztdemo_item
{
  key order_id as OrderId,
  key item_no  as ItemNo,
      product  as Product,
      @Semantics.amount.currencyCode: 'Currency'
      amount   as Amount,
      currency as Currency,
      canceled as IsCanceled
}
