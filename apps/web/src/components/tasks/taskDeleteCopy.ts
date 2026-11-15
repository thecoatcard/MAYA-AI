/** What deleting a finished task removes and keeps. Shown in every task delete dialog. */
export const TASK_DELETE_REMOVES: readonly string[] = [
  "The task, its steps and its result.",
  "The files MAYA made for it, from MAYA storage.",
  "Any approval still waiting for it is closed.",
];

export const TASK_DELETE_KEEPS: readonly string[] = [
  "The audit log and activity history.",
  "A copy saved in your working folder.",
];

export const CANCEL_FIRST = "Cancel the task first";
