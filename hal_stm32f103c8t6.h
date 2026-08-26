#ifndef HAL_STM32F103C8T6_H
#define HAL_STM32F103C8T6_H

/**
 * @file hal_stm32f103c8t6.h
 * @brief HAL configuration for STM32F103C8T6 (EOL)
 * 
 * NOTE: STM32F103C8T6 is discontinued per TI-PCN-2026-0731.
 * This file has been updated to route configurations to the recommended
 * replacement part: STM32F103CBT6 (pin-for-pin compatible, 2x flash capacity).
 */

#warning "STM32F103C8T6 is EOL. Compiling with STM32F103CBT6 configurations instead."

#define TARGET_MCU_STM32F103CBT6
#define TARGET_MCU_NAME "STM32F103CBT6"

// STM32F103CBT6 features 128KB Flash, 2x that of the original C8T6
#define FLASH_SIZE_KB 128
#define SRAM_SIZE_KB 20

#endif // HAL_STM32F103C8T6_H
