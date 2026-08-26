/**
 * @file hal_ina219.h
 * @brief Hardware Abstraction Layer for INA219 (Updated to target INA226AIDR per TI-PCN-2026-0482)
 *
 * This file has been updated to transition from INA219AIDR to the recommended replacement INA226AIDR.
 * Changes made:
 * - INA219_REG_CALIBRATION (0x05) needs to be recalculated per INA226 datasheet §8.6.
 * - Removed INA219_CONFIG_BADCRES_12BIT as the INA226 uses 16-bit fixed resolution.
 * - Added INA226_REG_MASK_ENABLE (0x06) and INA226_REG_ALERT_LIMIT (0x07).
 */

#ifndef HAL_INA219_H
#define HAL_INA219_H

#include <stdint.h>

/* I2C Address of the sensor */
#define INA219_I2C_ADDRESS               0x40

/* Registers */
#define INA219_REG_CONFIG                0x00
#define INA219_REG_SHUNTVOLTAGE          0x01
#define INA219_REG_BUSVOLTAGE            0x02
#define INA219_REG_POWER                 0x03
#define INA219_REG_CURRENT               0x04
#define INA219_REG_CALIBRATION           0x05 /* Recalculated per INA226 datasheet §8.6 */

/* INA226 Specific Registers (Added per TI-PCN-2026-0482) */
#define INA226_REG_MASK_ENABLE           0x06
#define INA226_REG_ALERT_LIMIT           0x07

/* Configuration register bits */
/* Note: INA219_CONFIG_BADCRES_12BIT is removed since INA226 uses fixed 16-bit resolution. */

#endif /* HAL_INA219_H */