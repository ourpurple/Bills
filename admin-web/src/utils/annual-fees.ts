/** Recurring dates are estimates, not evidence that a fee was charged or waived. */
export function nextAnnualFee(month: number | null, day: number | null, today = new Date()) {
  if (!month || !day) return null
  const ordinal = (year: number, m: number, d: number) => Date.UTC(year, m - 1, d)
  const start = ordinal(today.getFullYear(), today.getMonth() + 1, today.getDate())
  let year = today.getFullYear()
  // A February 29 anniversary is estimated on February 28 in non-leap years.
  const dateInYear = (y: number) => {
    const actualDay = Math.min(day, new Date(y, month, 0).getDate())
    return { time: ordinal(y, month, actualDay), day: actualDay }
  }
  let next = dateInYear(year)
  if (next.time < start) next = dateInYear(++year)
  return {
    date: `${year}-${String(month).padStart(2, '0')}-${String(next.day).padStart(2, '0')}`,
    days: Math.round((next.time - start) / 86_400_000),
    adjusted: next.day !== day,
  }
}
