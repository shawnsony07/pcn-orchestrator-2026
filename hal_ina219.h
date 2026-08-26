#ifndef HAL_INA219_H
#define HAL_INA219_H

#include <stdint.h>

/**
 * @file hal_ina219.h
 * @brief HAL Driver for INA219 (Updated to target replacement INA226AIDR per TI-PCN-2026-0482)
 *
 * The INA219AIDR is EOL and replaced by the INA226AIDR.
 * This header has been updated to maintain backward compatibility where possible
 * while incorporating the necessary register mappings and definitions for the INA226.
 */

/* I2C Address of INA226 (Same as INA219 default) */
#define INA219_I2C_ADDRESS             0x40

/* INA219 Register Map (Updated/Mapped to INA226 equivalent registers) */
#define INA219_REG_CONFIG              0x00
#define INA219_REG_SHUNT_VOLTAGE       0x01
#define INA219_REG_BUS_VOLTAGE         0x02
#define INA219_REG_POWER               0x03
#define INA219_REG_CURRENT             0x04

/**
 * @brief Calibration Register (0x05)
 * @note Recalculated per INA226 datasheet §8.6.
 *       INA226 Calibration register value = 0.00512 / (Current_LSB * Shunt_Resistor)
 */
#define INA219_REG_CALIBRATION         0x05

/* INA226 Additional Registers */
#define INA226_REG_MASK_ENABLE         0x06
#define INA226_REG_ALERT_LIMIT         0x07
#define INA226_REG_MANUFACTURER_ID     0xFE
#define INA226_REG_DIE_ID              0xFF

/* Configuration Register Fields (INA226 specific updates) */
/* Note: INA219_CONFIG_BADCRES_12BIT has been removed since INA226 uses 16-bit fixed resolution */

#define INA226_CONFIG_RESET            (1 << 15)
#define INA226_CONFIG_AVG_1            (0 << 9)
#define INA226_CONFIG_AVG_4            (1 << 9)
#define INA226_CONFIG_AVG_16           (2 << 9)
#define INA226_CONFIG_AVG_64           (3 << 9)

#define INA226_CONFIG_VBUS_CT_1_1MS    (4 << 6)
#define INA226_CONFIG_VSH_CT_1_1MS     (4 << 3)

#define INA226_CONFIG_MODE_CONTINUOUS  (7 << 0)

#endif /* HAL_INA219_H */
