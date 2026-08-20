import { Gauge } from 'lucide-react';
import { useFormatter, useTranslations } from 'use-intl';

import { TransportMenu } from './transport-menu';

import { Slider } from '@/components/ui/slider';

interface PlaybackSpeedMenuProps {
  open: boolean;
  playbackRate: number;
  onOpenChange: (open: boolean) => void;
  onHoverChange: (hovered: boolean) => void;
  onPlaybackRateChange: (rate: number) => void;
}

export function PlaybackSpeedMenu({
  open,
  playbackRate,
  onOpenChange,
  onHoverChange,
  onPlaybackRateChange,
}: PlaybackSpeedMenuProps) {
  const format = useFormatter();
  const t = useTranslations('viewer.transport');
  const sliderValue = playbackRate === 0.01 ? 0 : playbackRate;

  return (
    <TransportMenu
      open={open}
      label={t('playbackSpeed')}
      icon={Gauge}
      triggerClassName="max-sm:hidden"
      className="w-56 p-3"
      onOpenChange={onOpenChange}
      onHoverChange={onHoverChange}
    >
      <div className="flex flex-col gap-2">
        <span className="text-muted-foreground text-center text-xs font-medium tabular-nums">
          {t('speedValue', {
            speed: format.number(playbackRate, { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
          })}
        </span>
        <Slider
          variant="notched"
          orientation="horizontal"
          notchDivisor={5}
          min={0}
          max={2}
          step={0.05}
          value={[sliderValue]}
          onValueChange={([rate]) => {
            onPlaybackRateChange(rate === 0 ? 0.01 : (rate ?? 1));
          }}
        />
      </div>
    </TransportMenu>
  );
}
