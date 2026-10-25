"""One evaluation format; adapt only at the request and stored-output boundaries."""
import copy


def normalize(goal):
    if isinstance(goal, dict):
        return copy.deepcopy(goal)
    return {'common': goal} if goal else {}


def achieved(goals, progress):
    return bool(goals) and all(progress[key] == 'Achieved' for key in goals)


def stored_update(update, requested_goal):
    """Keep the existing scalar/common and object/individual result contract."""
    result = copy.deepcopy(update)
    if not isinstance(requested_goal, dict):
        result['progress'] = update['progress'].get('common', 'Unchanged')
        result['progress_reason'] = update['progress_reason'].get('common', 'No goal supplied.')
    return result
