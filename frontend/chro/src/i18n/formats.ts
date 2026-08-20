import { createFormatter, type DateTimeFormatOptions, type NumberFormatOptions } from 'use-intl';

import type { Locale } from './config';

interface DateTimeFormats extends Record<string, DateTimeFormatOptions> {
  date: DateTimeFormatOptions;
  dateLong: DateTimeFormatOptions;
  dateTime: DateTimeFormatOptions;
}

interface NumberFormats extends Record<string, NumberFormatOptions> {
  beat: NumberFormatOptions;
  decimal: NumberFormatOptions;
  integer: NumberFormatOptions;
  percent: NumberFormatOptions;
  precisePercent: NumberFormatOptions;
}

const dateTimeFormats: DateTimeFormats = {
  date: { dateStyle: 'medium' },
  dateLong: { dateStyle: 'long' },
  dateTime: {
    dateStyle: 'medium',
    timeStyle: 'medium',
  },
};
const numberFormats: NumberFormats = {
  beat: {
    minimumFractionDigits: 2,
    maximumFractionDigits: 3,
  },
  decimal: { maximumFractionDigits: 2 },
  integer: { maximumFractionDigits: 0 },
  percent: {
    style: 'percent',
    maximumFractionDigits: 1,
  },
  precisePercent: {
    style: 'percent',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  },
};

export const intlFormats = { dateTime: dateTimeFormats, number: numberFormats };

const formatters = new Map<Locale, ReturnType<typeof createFormatter>>();
const durationNumberFormat: NumberFormatOptions = {
  minimumIntegerDigits: 2,
  useGrouping: false,
};

export function getFormatter(locale: Locale) {
  const existing = formatters.get(locale);
  if (existing !== undefined) return existing;

  const formatter = createFormatter({
    formats: intlFormats,
    locale,
    timeZone: 'UTC',
  });
  formatters.set(locale, formatter);
  return formatter;
}

export function formatDuration(seconds: number, locale: Locale) {
  const totalSeconds = Math.max(0, Math.floor(seconds));
  const formatter = getFormatter(locale);
  return `${formatter.number(Math.floor(totalSeconds / 60), durationNumberFormat)}:${formatter.number(totalSeconds % 60, durationNumberFormat)}`;
}
