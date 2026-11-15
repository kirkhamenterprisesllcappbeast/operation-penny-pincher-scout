"""Conservative cost preflight. Public task text is evidence, never authority.

This module never installs software, connects wallets or authorizes payments.
Unknown costs remain unknown even when no textual warning is detected.
"""
import re
from decimal import Decimal
from decimal import InvalidOperation

COST_FIELDS = ('required_spending', 'execution_cost', 'platform_fees',
               'payment_fees', 'network_fees', 'recurring_costs', 'bond_at_risk')


def net_return_gate(economics):
    """Conservative arithmetic gate, not proof of inputs or promise of acceptance.

    Every amount must be an explicitly reviewed bound in the same currency.
    Count the full refundable bond as capital at risk. Future funding, cashback,
    deposits and advertised receipts never count as earnings.
    """
    blocked = {'status':'BLOCKED_UNKNOWN_ECONOMICS','qualifies':False,
               'conservative_net':None,'currency':None}
    if not isinstance(economics, dict): return blocked
    if economics.get('paid_subscription_required') is True:
        return {**blocked,'status':'BLOCKED_PAID_SUBSCRIPTION_REQUIRED'}
    if (not isinstance(economics.get('currency'), str)
        or not re.fullmatch(r'[A-Z]{3,12}', economics['currency'])
        or economics.get('cost_currency') != economics['currency']):
        return {**blocked,'status':'BLOCKED_CURRENCY_UNCONFIRMED'}
    try:
        values = []
        for key in ('reward_lower_bound',) + COST_FIELDS:
            raw = economics.get(key)
            if not isinstance(raw, (str, int, Decimal)) or isinstance(raw, bool): return blocked
            value = Decimal(raw)
            if not value.is_finite() or value < 0 or value > Decimal('1000000000000'): return blocked
            values.append(value)
        net = values[0] - sum(values[1:], Decimal(0))
    except (InvalidOperation, ValueError): return blocked
    if net <= 0:
        return {'status':'REJECTED_NONPOSITIVE_NET_RETURN','qualifies':False,
                'conservative_net':str(net),'currency':economics['currency']}
    return {'status':'POSITIVE_NET_REQUIRES_SOURCE_AND_ELIGIBILITY_REVIEW',
            'qualifies':True,'conservative_net':str(net),'currency':economics['currency']}


def assess_costs(description, reward=None):
    if not isinstance(description, str):
        raise ValueError('Description must be text')
    evidence = []
    # This expresses required provider revenue, not necessarily the operator's
    # total expense. Retain the exact excerpt and do not silently equate them.
    pattern = r'at least\s+(\d{1,9}(?:\.\d{1,6})?)\s+USDC\s+of\s+(?:attributable\s+)?settled Industrial Platform revenue'
    thresholds = []
    for match in re.finditer(pattern, description, re.I):
        thresholds.append(Decimal(match.group(1)))
        evidence.append({'kind': 'provider_revenue_threshold', 'excerpt': match.group(0)})
    text = description.lower()
    paid_usage = any(term in text for term in ('paid cycle', 'paid cycles', 'settled sentinel call', 'bounded payer configuration'))
    deployment = any(term in text for term in ('container:', 'software deployment', 'sentinel container'))
    reasons = []
    if any(term in text for term in ('paid subscription', 'subscription required', 'premium subscription', 'monthly subscription')):
        reasons.append('SUBSCRIPTION_REQUIREMENT_REVIEW_REQUIRED')
    if paid_usage:
        reasons.append('PAID_EXTERNAL_USAGE_REQUIRED')
    if deployment:
        reasons.append('THIRD_PARTY_DEPLOYMENT_APPROVAL_REQUIRED')
    threshold = max(thresholds) if thresholds else None
    if reward is not None and threshold is not None and threshold > Decimal(reward):
        reasons.append('REQUIRED_PROVIDER_REVENUE_EXCEEDS_REWARD')
    return {'reasons': reasons, 'paid_usage_required': paid_usage,
            'required_provider_revenue_usdc': str(threshold) if threshold is not None else None,
            'operator_expense_usdc': None, 'evidence': evidence,
            'note': 'Required provider revenue is not an independently verified operator expense. Absence of detected costs does not establish free execution.'}


def bond_preflight(*, funds_confirmed=False, registered_agent=False,
                   wallet_ownership_confirmed=False, transaction_approved=False,
                   task_qualified=False):
    """Readiness checklist only; booleans are assertions, never authorization.

    A promise to provide funds must leave funds_confirmed=False. Even all true
    cannot generate a signed transaction or an execution capability.
    """
    checks = {'funds_confirmed': funds_confirmed, 'registered_agent': registered_agent,
              'wallet_ownership_confirmed': wallet_ownership_confirmed,
              'transaction_approved': transaction_approved, 'task_qualified': task_qualified}
    if any(type(value) is not bool for value in checks.values()):
        raise ValueError('Checklist values must be booleans')
    return {'status': 'REVIEW_REQUIRED' if all(checks.values()) else 'BLOCKED',
            'missing': [key for key, value in checks.items() if not value],
            'bond_usdc': '1', 'capital_at_risk': True,
            'funds_promised_are_funds_received': False,
            'transaction_authorized_by_checklist': False,
            'transactions_sent': 0}
