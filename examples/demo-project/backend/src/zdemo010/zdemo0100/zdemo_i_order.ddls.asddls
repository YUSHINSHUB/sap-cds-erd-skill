@AccessControl.authorizationCheck: #NOT_REQUIRED
@EndUserText.label: '주문 헤더'
define view entity ZDEMO_I_ORDER
  as select from    ztdemo_order as _Order
    left outer join I_Customer   as _Customer on _Customer.Customer = _Order.customer
{
  key _Order.order_id   as OrderId,
      _Order.customer   as Customer,
      _Customer.CustomerName,
      _Order.order_date as OrderDate,
      _Order.status     as Status
}
