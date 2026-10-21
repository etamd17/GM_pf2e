export function actionFeedback(outcome: string): string {
  const messages: Record<string,string> = {
    stale: 'The tracker changed. Review the current state before trying again.',
    'not-live': 'This campaign is not the live session. Open its session before making changes.',
    'no-encounter': 'Start an encounter in the tracker first.',
    'not-in-encounter': 'This creature is not in the current encounter.',
    'no-encounter-token': 'Wait for the encounter to load, then refresh the search or review.',
    'no-targets': 'Select at least one linked encounter creature.',
    unsupported: 'This action is not available for this game system.',
    invalid: 'Check the values and targets before trying again.',
    blocked: 'The action was refused. Check the live session and your GM access.',
    'not-found': 'That creature is no longer available. Search again.',
    busy: 'An action is already being sent.',
    failed: 'The result could not be confirmed. Check the tracker before trying again.',
  };
  return messages[outcome] ?? 'The action could not be completed.';
}
