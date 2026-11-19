@AccessControl.authorizationCheck: #NOT_REQUIRED
@EndUserText.label: '주문별 금액 합계'
@ObjectModel.usageType:{
    serviceQuality: #X,
    sizeCategory: #S,
    dataClass: #MIXED
}
define view entity ZDEMO_I_ORDER_AMOUNT
  as select from ZDEMO_I_ORDER_ITEM
{
  key OrderId,

      @Semantics.amount.currencyCode: 'Currency'
      sum( Amount ) as TotalAmount,

      Currency
}
where
  IsCanceled = ''
group by
  OrderId,
  Currency
