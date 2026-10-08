/**
 * CrewmateLiveActivity — the one status line a crewmate's chat shows while its
 * turn runs: what the crewmate is doing right now (#18238).
 *
 * It sits in the footer column directly above the working indicator (the
 * ghost-pose carousel in `ChatFooter`), not in the transcript. That placement
 * is the point: a transcript row mounting and unmounting on every tool step
 * hopped the indicator up and down by a row each time. This line is ONE
 * fixed-height row, mounted for the WHOLE live turn, whose text swaps in place
 * — so the indicator under it never moves while the crewmate works.
 *
 * The text is the DM header pill's own reading (`useSlotActivity` →
 * `resolvePillActivity`, over the slot's live status record and the shared
 * `toolStatusLabel`), so the chat and the header name one moment the same way.
 * Words only: the ghost under the line already carries the motion, and a
 * second spinner read as a second "working" sign.
 */
import { useTranslation } from 'react-i18next'
import { motion, useReducedMotion } from 'framer-motion'
import { PILL_ACTIVITY_KEY, type PillActivity, type PillActivityKind } from '../../pages/members/pillActivity'

export default function CrewmateLiveActivity({ activity }: { activity: PillActivity }) {
  const { t } = useTranslation()
  const reduce = useReducedMotion()
  const label = activity.text !== undefined
    ? activity.text
    : t(PILL_ACTIVITY_KEY[activity.kind as Exclude<PillActivityKind, 'tool' | 'thinking'>])
  return (
    <div
      data-testid="crewmate-live-activity"
      data-activity={activity.kind}
      className="px-4 mx-auto w-full py-1"
      style={{ maxWidth: 'var(--mc-content-width, 900px)' }}
    >
      {/* One line, pinned: `h-5` + `truncate` so a long label can never add a
          second line and move the indicator below. */}
      <div className="flex items-center h-5 min-w-0 text-[13px] text-muted" role="status">
        {/* Keyed by the text so a new step fades in over the old text's place;
            the old span simply leaves — no exit animation, nothing to shift. */}
        <motion.span
          key={label}
          className="min-w-0 truncate leading-5"
          initial={reduce ? false : { opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.25 }}
        >
          {label}
        </motion.span>
      </div>
    </div>
  )
}
