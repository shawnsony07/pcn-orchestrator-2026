#ifndef HAL_INA219_H
#define HAL_INA219_H

#include <stdint.h>

/**
 * @file hal_ina219.h
 * @brief HAL driver for INA219AIDR updated to target INA226AIDR per TI-PCN-2026-0482.
 */

/* I2C Address (Unchanged) */
#define INA219_I2C_ADDRESS               0x40

/* Registers */
#define INA219_REG_CONFIG                0x00
#define INA219_REG_SHUNT_VOLTAGE         0x01
#define INA219_REG_BUS_VOLTAGE           0x02
#define INA219_REG_POWER                 0x03
#define INA219_REG_CURRENT               0x04

/* Recalculated per INA226 datasheet §8.6 */
#define INA219_REG_CALIBRATION           0x05

/* New Registers for INA226 */
#define INA226_REG_MASK_ENABLE           0x06
#define INA226_REG_ALERT_LIMIT           0x07

/* Configuration Register Mask / Bits */
/* INA219_CONFIG_BADCRES_12BIT removed as INA226 uses 16-bit fixed resolution */

#endif /* HAL_INA219_H */
