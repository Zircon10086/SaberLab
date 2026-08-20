import { useRef, type ReactNode } from 'react';

import type { LucideIcon } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';

interface TransportMenuProps {
  children: ReactNode;
  className?: string;
  disabled?: boolean;
  icon: LucideIcon;
  label: string;
  open: boolean;
  triggerClassName?: string;
  desktopLabel?: string;
  onDesktopTriggerClick?: () => void;
  onHoverChange: (hovered: boolean) => void;
  onOpenChange: (open: boolean) => void;
}

export function TransportMenu({
  children,
  className,
  disabled = false,
  icon: Icon,
  label,
  open,
  triggerClassName,
  desktopLabel = label,
  onDesktopTriggerClick,
  onHoverChange,
  onOpenChange,
}: TransportMenuProps) {
  const interactionPointerRef = useRef('');
  const openedOnHoverRef = useRef(false);

  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger asChild>
        <Button
          className={triggerClassName}
          variant="ghost"
          size="icon"
          disabled={disabled}
          aria-label={label}
          title={desktopLabel}
          onPointerDown={(event) => {
            interactionPointerRef.current = event.pointerType;
            if (event.pointerType !== 'mouse') openedOnHoverRef.current = false;
          }}
          onPointerEnter={(event) => {
            if (event.pointerType !== 'mouse') return;
            openedOnHoverRef.current = true;
            onHoverChange(true);
          }}
          onPointerLeave={(event) => {
            if (event.pointerType === 'mouse') onHoverChange(false);
          }}
          onClick={(event) => {
            if (event.detail === 0 || interactionPointerRef.current !== 'mouse') {
              openedOnHoverRef.current = false;
              return;
            }
            event.preventDefault();
            onDesktopTriggerClick?.();
          }}
        >
          <Icon data-icon="inline-start" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        side="top"
        align="center"
        sideOffset={12}
        className={className}
        onOpenAutoFocus={(event) => {
          if (openedOnHoverRef.current) event.preventDefault();
        }}
        onCloseAutoFocus={(event) => {
          if (openedOnHoverRef.current) event.preventDefault();
        }}
        onPointerEnter={(event) => {
          if (event.pointerType === 'mouse') onHoverChange(true);
        }}
        onPointerLeave={(event) => {
          if (event.pointerType === 'mouse') onHoverChange(false);
        }}
      >
        {children}
      </PopoverContent>
    </Popover>
  );
}
