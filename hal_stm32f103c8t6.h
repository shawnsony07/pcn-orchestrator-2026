#ifndef HAL_STM32F103C8T6_H
#define HAL_STM32F103C8T6_H

/*
 * PCN-driven HAL Update
 * Affected Part: STM32F103C8T6 (EOL)
 * Recommended Replacement: STM32F103CBT6 (pin-for-pin compatible, 2x flash capacity)
 */

#warning "STM32F103C8T6 is EOL. Migrating HAL to STM32F103CBT6."

#define TARGET_MCU_STM32F103CBT6
#define FLASH_SIZE_KB 128

#endif // HAL_STM32F103C8T6_H