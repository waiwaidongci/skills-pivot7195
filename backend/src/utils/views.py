from src.utils.time import iso


def dataset(item):
    return {'id': item.id, 'name': item.name, 'filename': item.filename, 'row_count': item.row_count,
            'checksum': item.checksum, 'author': item.author, 'created_at': iso(item.created_at)}


def run(item, detail=False):
    item_view = {'id': item.id, 'dataset_id': item.dataset_id, 'name': item.name, 'status': item.status,
                 'author': item.author, 'review': item.review, 'decision': item.decision,
                 'created_at': iso(item.created_at), 'finished_at': iso(item.finished_at),
                 'scheduled_at': iso(item.scheduled_at), 'schedule_id': item.schedule_id,
                 'has_export': bool(item.export_file), 'error': item.error}
    if detail:
        item_view.update(params=item.params, result=item.result, review_note=item.review_note)
    return item_view


def schedule(item):
    from src.domain.spec import SPEC
    return {'id': item.id, 'name': item.name, 'dataset_id': item.dataset_id, 'recipient': item.recipient,
            'next_at': iso(item.next_at), 'repeat_daily': item.repeat_daily, 'enabled': item.enabled,
            'next_report_day': item.params.get(SPEC['schedule_date_param']) if item.enabled else None}


def delivery(item):
    return {'id': item.id, 'run_id': item.run_id, 'recipient': item.recipient,
            'state': item.state, 'detail': item.detail, 'created_at': iso(item.created_at)}


def backtest(item, detail=False, names=None):
    from src.domain.backtest import maturity_date, settlement_ready
    from src.utils.time import now
    names = names or {}
    prediction, actual = item.prediction, item.actual
    item_view = {'id': item.id, 'name': item.name, 'period': item.period, 'status': item.status,
                 'dataset_id': item.dataset_id,
                 'dataset_name': names.get(item.dataset_id),
                 'actual_dataset_id': item.actual_dataset_id,
                 'actual_dataset_name': names.get(item.actual_dataset_id),
                 'author': item.author, 'error': item.error, 'created_at': iso(item.created_at),
                 'predicted_at': iso(item.predicted_at), 'settled_at': iso(item.settled_at),
                 'maturity_date': maturity_date(item.period),
                 'settlement_ready': settlement_ready(item.period, now()),
                 'has_prediction': prediction is not None, 'has_actual': actual is not None,
                 'estimated_enrolled': prediction['estimated_enrolled'] if prediction else None,
                 'actual_enrolled': actual['actual_enrolled'] if actual else None,
                 'error_value': actual['error'] if actual else None,
                 'flags': (actual or prediction or {}).get('flags', [])}
    if detail:
        item_view.update(prediction=prediction, actual=actual)
    return item_view
