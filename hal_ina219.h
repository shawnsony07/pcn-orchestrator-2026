#ifndef HAL_INA219_H
#define HAL_INA219_H

#include <stdint.h>

/*
 * HAL Driver for INA219 / INA226
 * Updated to target INA226AIDR per TI-PCN-2026-0482
 */

// I2C default address for INA219/INA226
#define INA219_I2C_ADDR                 0x40

// Register Addresses
#define INA219_REG_CONFIG               0x00
#define INA219_REG_SHUNT_VOLTAGE        0x01
#define INA219_REG_BUS_VOLTAGE          0x02
#define INA219_REG_POWER                0x03
#define INA219_REG_CURRENT              0x04
#define INA219_REG_CALIBRATION          0x05 // Recalculate per INA226 datasheet §8.6

// INA226 Specific Registers (Added per PCN-2026-0482)
#define INA226_REG_MASK_ENABLE          0x06
#define INA226_REG_ALERT_LIMIT          0x07

// Configuration register values
#define INA219_CONFIG_RESET             0x8000

// Resolution configurations
// INA219_CONFIG_BADCRES_12BIT is removed as INA226 uses 16-bit fixed resolution

// Function declarations
int hal_ina219_init(uint8_t i2c_addr);
int hal_ina219_read_current(uint8_t i2c_addr, int16_t *current_ma);
int hal_ina219_read_bus_voltage(uint8_t i2c_addr, int16_t *voltage_mv);
int hal_ina219_read_power(uint8_t i2c_addr, int32_t *power_mw);

#endif // HAL_INA219_H
