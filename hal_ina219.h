#ifndef HAL_INA219_H
#define HAL_INA219_H

/**
 * @file hal_ina219.h
 * @brief HAL driver header updated to target INA226AIDR (pin/I2C-compatible replacement for INA219AIDR).
 * 
 * Target Replacement: INA226AIDR
 * Reference: TI-PCN-2026-0482
 */

/* INA219 Register Definitions updated/recalculated for INA226 */
#define INA219_REG_CONFIG              0x00
#define INA219_REG_SHUNTVOLTAGE        0x01
#define INA219_REG_BUSVOLTAGE          0x02
#define INA219_REG_POWER               0x03
#define INA219_REG_CURRENT             0x04

/* Calibration register - value must be recalculated per INA226 datasheet section 8.6 */
#define INA219_REG_CALIBRATION         0x05

/* INA226 Specific Registers */
#define INA226_REG_MASK_ENABLE         0x06
#define INA226_REG_ALERT_LIMIT         0x07

/* Note: INA219_CONFIG_BADCRES_12BIT has been removed since INA226 uses 16-bit fixed resolution. */

#endif /* HAL_INA219_H */
