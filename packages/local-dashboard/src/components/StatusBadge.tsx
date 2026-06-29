import type { MemoryStatus } from "../lib/api";

export interface StatusBadgeProps {
  status: MemoryStatus | string;
}

/**
 * Small pill showing a memory lifecycle status (active / corrected / expired /
 * deleted / disputed). Color is keyed off `data-status` in brand.css.
 */
export function StatusBadge({ status }: StatusBadgeProps) {
  return (
    <span className="mg-status" data-status={status} aria-label={`status ${status}`}>
      {status}
    </span>
  );
}

export default StatusBadge;
