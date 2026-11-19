@AccessControl.authorizationCheck: #NOT_REQUIRED
@EndUserText.label: '주문 (BO)'
define root view entity ZDEMO_R_ORDER
  as select from ZDEMO_I_ORDER
  composition [0..*] of ZDEMO_R_ORDER_ITEM      as _Item
  association [1]    to ZDEMO_I_ORDER_AMOUNT    as _Amount   on $projection.OrderId = _Amount.OrderId
  association [0..1] to ZDEMO_I_ORDER_APPR_LAST as _LastAppr on $projection.OrderId = _LastAppr.OrderId
  association [0..1] to I_Currency              as _Currency on $projection.Currency = _Currency.Currency
{
  key OrderId,
      Customer,
      CustomerName,
      OrderDate,
      Status,

      @Semantics.amount.currencyCode: 'Currency'
      _Amount.TotalAmount,

      _Amount.Currency,

      _LastAppr.LastApprovalNo,

      /* Associations */
      _Item,
      _Currency
}
