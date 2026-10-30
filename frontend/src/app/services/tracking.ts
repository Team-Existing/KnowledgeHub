import { CommitmentStatus, Verdict } from '../models/api'

export const VERDICT_LABEL: Record<Verdict, string> = {
  achieved: 'Achieved',
  partly: 'Partly achieved',
  not_achieved: 'Not achieved',
}

export const COMMITMENT_LABEL: Record<CommitmentStatus, string> = {
  open: 'Open',
  in_progress: 'In progress',
  done: 'Done',
  dropped: 'Dropped',
}
