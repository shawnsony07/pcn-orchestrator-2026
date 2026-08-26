#ifndef HAL_INA219_H
#define HAL_INA219_H

#include <stdint.h>

/*
 * HAL Driver for INA219AIDR updated to target INA226AIDR.
 * PCN Reference: TI-PCN-2026-0482
 */

// I2C Device Address (unchanged, pin-compatible)
#define INA219_I2C_ADDR                  0x40

// Register Map
#define INA219_REG_CONFIG                0x00
#define INA219_REG_SHUNTVOLTAGE          0x01
#define INA219_REG_BUSVOLTAGE            0x02
#define INA219_REG_POWER                 0x03
#define INA219_REG_CURRENT               0x04

// INA219_REG_CALIBRATION (0x05) -> Recalculate per INA226 datasheet §8.6
#define INA219_REG_CALIBRATION           0x05

// INA226 specific registers added
#define INA226_REG_MASK_ENABLE           0x06
#define INA226_REG_ALERT_LIMIT           0x07

// Configuration Bit Fields
#define INA219_CONFIG_RESET              0x8000

// Note: INA219_CONFIG_BADCRES_12BIT has been removed because INA226 uses 16-bit fixed resolution.

#endif // HAL_INA219_H